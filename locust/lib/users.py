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


def _iter_sse_events(lines, on_line=None):
    """Yield (event type, parsed data) from an SSE response."""
    event_type = None
    data_lines = []

    for line in lines:
        if on_line:
            on_line(line)
        text = line.decode() if isinstance(line, bytes) else line
        text = text.rstrip("\r")
        if not text:
            if data_lines:
                data = "\n".join(data_lines)
                if data != "[DONE]":
                    try:
                        yield event_type or "message", json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("Ignoring invalid SSE data: %s", data)
            event_type = None
            data_lines = []
        elif text.startswith("event:"):
            event_type = text[6:].strip()
        elif text.startswith("data:"):
            data_lines.append(text[5:].lstrip())

    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            try:
                yield event_type or "message", json.loads(data)
            except json.JSONDecodeError:
                logger.debug("Ignoring invalid SSE data: %s", data)


class LCSBaseUser(HttpUser):
    """Base user with auth headers and zero wait time between requests."""

    abstract = True
    wait_time = constant(0)

    def on_start(self):
        self.conversation_id = None
        self.previous_response_id = None
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

            stream_failed = False

            def count_bytes(line):
                nonlocal response_bytes
                response_bytes += len(line) if isinstance(line, bytes) else len(line.encode())

            for event_type, event_data in _iter_sse_events(response.iter_lines(), count_bytes):
                if not isinstance(event_data, dict):
                    continue

                # /v1/streaming_query usually carries the event name in JSON body
                # as {"event": "...", "data": {...}} rather than SSE "event:" lines.
                payload_event = event_data.get("event") or event_type

                if payload_event == "token" and ttft is None:
                    ttft = (time.perf_counter() - start) * 1000
                    record_ttft(ttft)

                if payload_event == "error":
                    stream_failed = True

                if not self.conversation_id:
                    payload_data = event_data.get("data")
                    if isinstance(payload_data, dict):
                        cid = payload_data.get("conversation_id")
                        if cid:
                            self.conversation_id = cid

            total_stream_ms = (time.perf_counter() - start) * 1000
            logger.debug("Stream complete: ttft=%.1fms total=%.1fms bytes=%d",
                          ttft or 0, total_stream_ms, response_bytes)
            record_stream_time(total_stream_ms)
            record_bytes(response_bytes, payload_bytes)

            if stream_failed:
                response.failure("Streaming query reported a failure")
            else:
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
    """Simulated user sending POST /v1/responses (non-streaming)."""

    @task
    def responses(self):
        payload = {
            "input": random.choice(QUESTIONS),
            "model": _RESPONSES_MODEL,
            "stream": False,
            "store": True,
        }
        if self.previous_response_id:
            payload["previous_response_id"] = self.previous_response_id

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
                    self.previous_response_id = data.get("id")
                except (TypeError, ValueError):
                    response.failure("Invalid JSON response")
                    return
                record_bytes(len(response.content or b""), payload_bytes)
                response.success()
            else:
                logger.debug("Responses failed: status=%d", response.status_code)
                response.failure(f"Status {response.status_code}")


class LCSStreamingResponsesClient(LCSBaseUser):
    """Simulated user sending POST /v1/responses with stream=True."""

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
        }
        if self.previous_response_id:
            payload["previous_response_id"] = self.previous_response_id

        payload_bytes = len(json.dumps(payload).encode())
        start = time.perf_counter()
        ttft = None
        response_bytes = 0
        stream_failed = False

        def count_bytes(line):
            nonlocal response_bytes
            response_bytes += len(line) if isinstance(line, bytes) else len(line.encode())

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

            for event_type, event_data in _iter_sse_events(response.iter_lines(), count_bytes):
                if not isinstance(event_data, dict):
                    continue
                response_data = event_data.get("response", {})
                if event_type in {"response.created", "response.completed"}:
                    response_id = response_data.get("id")
                    if response_id:
                        self.previous_response_id = response_id
                if event_type == "response.output_text.delta" and ttft is None:
                    ttft = (time.perf_counter() - start) * 1000
                    record_ttft(ttft)
                if event_type in {"error", "response.failed", "response.incomplete"}:
                    stream_failed = True

            total_stream_ms = (time.perf_counter() - start) * 1000
            logger.debug("Responses stream complete: ttft=%.1fms total=%.1fms bytes=%d",
                         ttft or 0, total_stream_ms, response_bytes)
            record_stream_time(total_stream_ms)
            record_bytes(response_bytes, payload_bytes)

            if stream_failed:
                response.failure("Responses stream reported a failure")
            else:
                response.success()

            events.request.fire(
                request_type="SSE",
                name="/v1/responses [full stream]",
                response_time=total_stream_ms,
                response_length=response_bytes,
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
