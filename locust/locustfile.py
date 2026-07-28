"""LCS Performance Test — Locust entry point.

Endpoint mode controlled by ENDPOINT_TYPE env var:
  query      → POST /v1/query
  streaming  → POST /v1/streaming_query  (SSE with TTFT measurement)

"""

import logging

from lib.config import ENDPOINT_TYPE
from lib.metrics import *  # noqa: F401,F403 — registers event listeners

logger = logging.getLogger("lcs.locustfile")

if ENDPOINT_TYPE == "streaming":
    from lib.users import LCSStreamingClient as ActiveUser
    logger.debug("Loaded streaming user class: LCSStreamingClient")
else:
    from lib.users import LCSQueryClient as ActiveUser
    logger.debug("Loaded query user class: LCSQueryClient")
