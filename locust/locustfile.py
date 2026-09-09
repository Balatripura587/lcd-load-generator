"""LCS Performance Test — Locust entry point.

Endpoint mode controlled by ENDPOINT_TYPE env var:
  query               → POST /v1/query
  streaming           → POST /v1/streaming_query        (SSE with TTFT)
  responses           → POST /v1/responses stream=False (OpenAI-compatible)
  streaming_responses → POST /v1/responses stream=True  (SSE with TTFT)

Works with both library mode and server mode — controlled by LCS_HOST env var.
"""

import logging

from lib.config import ENDPOINT_TYPE
from lib.metrics import on_test_start, on_test_stop  # noqa: F401 — registers event listeners

logger = logging.getLogger("lcs.locustfile")

if ENDPOINT_TYPE == "streaming":
    from lib.users import LCSStreamingClient as ActiveUser
    logger.debug("Loaded streaming user class: LCSStreamingClient")
elif ENDPOINT_TYPE == "responses":
    from lib.users import LCSResponsesClient as ActiveUser
    logger.debug("Loaded responses user class: LCSResponsesClient")
elif ENDPOINT_TYPE == "streaming_responses":
    from lib.users import LCSStreamingResponsesClient as ActiveUser
    logger.debug("Loaded streaming responses user class: LCSStreamingResponsesClient")
else:
    from lib.users import LCSQueryClient as ActiveUser
    logger.debug("Loaded query user class: LCSQueryClient")
