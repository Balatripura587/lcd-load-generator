"""Locust user classes simulating LCS query and streaming endpoint traffic."""

import json
import logging
import random
import time

from locust import HttpUser, constant, events, task

from lib.config import LCS_MODEL, LCS_PROVIDER, LCS_TOKEN, REQUEST_TIMEOUT
from lib.metrics import record_bytes, record_stream_time, record_ttft
from lib.questions import QUESTIONS

logger = logging.getLogger("lcs.users")

# Responses API uses combined "provider/model" format
_RESPONSES_MODEL = f"{LCS_PROVIDER}/{LCS_MODEL}"


class LCSBaseUser(HttpUser):
    """Base user with auth headers and zero wait time between requests."""

    abstract = True
    wait_time = constant(0)

    def on_start(self):
        self.conversation_id = None
        self.headers = {"Content-Type": "application/json"}
        if LCS_TOKEN:
            self.headers["Authorization"] = f"Bearer {LCS_TOKEN}"
        logger.debug("User started: provider=%s model=%s token=%s",
                      LCS_PROVIDER, LCS_MODEL, "set" if LCS_TOKEN else "none")


class LCSQueryClient(LCSBaseUser):
    """Simulated user sending POST /v1/query requests."""

    @task
    def query(self):
        payload = {
            "query": random.choice(QUESTIONS),
            "provider": LCS_PROVIDER,
            "model": LCS_MODEL,
            "no_tools": True,
            "generate_topic_summary": False,
        }
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id

        payload_bytes = len(json.dumps(payload).encode())

        with self.client.post(
            "/v1/query",
            json=payload,
            headers=self.headers,
            timeout=REQUEST_TIMEOUT,
            name="/v1/query",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    new_cid = data.get("conversation_id")
                    if new_cid and not self.conversation_id:
                        logger.debug("New conversation: %s", new_cid)
                    self.conversation_id = new_cid or self.conversation_id
                except Exception:
                    pass
                record_bytes(len(response.content or b""), payload_bytes)
                response.success()
            else:
                logger.debug("Query failed: status=%d", response.status_code)
                response.failure(f"Status {response.status_code}")


class LCSStreamingClient(LCSBaseUser):
    """Simulated user sending POST /v1/streaming_query SSE requests with TTFT tracking."""

    def on_start(self):
        super().on_start()
        self.headers["Accept"] = "text/event-stream"

    @task
    def streaming_query(self):
        payload = {
            "query": random.choice(QUESTIONS),
            "provider": LCS_PROVIDER,
            "model": LCS_MODEL,
            "no_tools": True,
            "generate_topic_summary": False,
        }
        if self.conversation_id:
            payload["conversation_id"] = self.conversation_id

        payload_bytes = len(json.dumps(payload).encode())
        start = time.perf_counter()
        ttft = None
        response_bytes = 0

        with self.client.post(
            "/v1/streaming_query",
            json=payload,
            headers=self.headers,
            stream=True,
            catch_response=True,
            timeout=REQUEST_TIMEOUT,
            name="/v1/streaming_query",
        ) as response:
            if response.status_code != 200:
                logger.debug("Streaming failed: status=%d", response.status_code)
                response.failure(f"Status {response.status_code}")
                return

            for line in response.iter_lines():
                if line:
                    response_bytes += len(line) if isinstance(line, bytes) else len(line.encode())
                    if ttft is None:
                        ttft = (time.perf_counter() - start) * 1000
                        record_ttft(ttft)
                    if not self.conversation_id:
                        try:
                            text = line.decode() if isinstance(line, bytes) else line
                            if text.startswith("data:"):
                                event_data = json.loads(text[5:].strip())
                                cid = event_data.get("conversation_id")
                                if cid:
                                    self.conversation_id = cid
                        except Exception:
                            pass

            total_stream_ms = (time.perf_counter() - start) * 1000
            logger.debug("Stream complete: ttft=%.1fms total=%.1fms bytes=%d",
                          ttft or 0, total_stream_ms, response_bytes)
            record_stream_time(total_stream_ms)
            record_bytes(response_bytes, payload_bytes)

            response.success()
            events.request.fire(
                request_type="SSE",
                name="/v1/streaming_query [full stream]",
                response_time=total_stream_ms,
                response_length=0,
                exception=None,
                context={"synthetic": True},
            )
            if ttft is not None:
                events.request.fire(
                    request_type="SSE",
                    name="/v1/streaming_query [TTFT]",
                    response_time=ttft,
                    response_length=0,
                    exception=None,
                    context={"synthetic": True},
                )


class LCSResponsesClient(LCSBaseUser):
    """Simulated user sending POST /v1/responses (non-streaming, OpenAI-compatible)."""

    @task
    def responses(self):
        payload = {
            "input": random.choice(QUESTIONS),
            "model": _RESPONSES_MODEL,
            "stream": False,
            "store": True,
            "generate_topic_summary": False,
        }
        if self.conversation_id:
            payload["conversation"] = self.conversation_id

        payload_bytes = len(json.dumps(payload).encode())

        with self.client.post(
            "/v1/responses",
            json=payload,
            headers=self.headers,
            timeout=REQUEST_TIMEOUT,
            name="/v1/responses",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    if not self.conversation_id:
                        self.conversation_id = data.get("conversation_id")
                except Exception:
                    pass
                record_bytes(len(response.content or b""), payload_bytes)
                response.success()
            else:
                logger.debug("Responses failed: status=%d", response.status_code)
                response.failure(f"Status {response.status_code}")


class LCSStreamingResponsesClient(LCSBaseUser):
    """Simulated user sending POST /v1/responses with stream=True (SSE) with TTFT tracking."""

    def on_start(self):
        super().on_start()
        self.headers["Accept"] = "text/event-stream"

    @task
    def streaming_responses(self):
        payload = {
            "input": random.choice(QUESTIONS),
            "model": _RESPONSES_MODEL,
            "stream": True,
            "store": True,
            "generate_topic_summary": False,
        }
        if self.conversation_id:
            payload["conversation"] = self.conversation_id

        payload_bytes = len(json.dumps(payload).encode())
        start = time.perf_counter()
        ttft = None
        response_bytes = 0

        with self.client.post(
            "/v1/responses",
            json=payload,
            headers=self.headers,
            stream=True,
            catch_response=True,
            timeout=REQUEST_TIMEOUT,
            name="/v1/responses [streaming]",
        ) as response:
            if response.status_code != 200:
                logger.debug("Streaming responses failed: status=%d", response.status_code)
                response.failure(f"Status {response.status_code}")
                return

            for line in response.iter_lines():
                if line:
                    response_bytes += len(line) if isinstance(line, bytes) else len(line.encode())
                    if ttft is None:
                        ttft = (time.perf_counter() - start) * 1000
                        record_ttft(ttft)
                    if not self.conversation_id:
                        try:
                            text = line.decode() if isinstance(line, bytes) else line
                            if text.startswith("data:"):
                                event_data = json.loads(text[5:].strip())
                                cid = event_data.get("conversation_id")
                                if cid:
                                    self.conversation_id = cid
                        except Exception:
                            pass

            total_stream_ms = (time.perf_counter() - start) * 1000
            record_stream_time(total_stream_ms)
            record_bytes(response_bytes, payload_bytes)

            response.success()
            events.request.fire(
                request_type="SSE",
                name="/v1/responses [full stream]",
                response_time=total_stream_ms,
                response_length=0,
                exception=None,
                context={"synthetic": True},
            )
            if ttft is not None:
                events.request.fire(
                    request_type="SSE",
                    name="/v1/responses [TTFT]",
                    response_time=ttft,
                    response_length=0,
                    exception=None,
                    context={"synthetic": True},
                )
