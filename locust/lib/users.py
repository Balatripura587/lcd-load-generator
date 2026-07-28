import json
import random
import time

from locust import HttpUser, constant, events, task

from lib.config import LCS_MODEL, LCS_PROVIDER, LCS_TOKEN, REQUEST_TIMEOUT
from lib.metrics import record_bytes, record_stream_time, record_ttft
from lib.questions import QUESTIONS


class LCSBaseUser(HttpUser):
    abstract = True
    wait_time = constant(0)

    def on_start(self):
        self.conversation_id = None
        self.headers = {"Content-Type": "application/json"}
        if LCS_TOKEN:
            self.headers["Authorization"] = f"Bearer {LCS_TOKEN}"


class LCSQueryClient(LCSBaseUser):

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
                    self.conversation_id = data.get("conversation_id", self.conversation_id)
                except Exception:
                    pass
                record_bytes(len(response.content or b""), payload_bytes)
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


class LCSStreamingClient(LCSBaseUser):

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
                name="/v1/streaming_query [full stream]",
                response_time=total_stream_ms,
                response_length=0,
                exception=None,
                context={},
            )
            if ttft is not None:
                events.request.fire(
                    request_type="SSE",
                    name="/v1/streaming_query [TTFT]",
                    response_time=ttft,
                    response_length=0,
                    exception=None,
                    context={},
                )
