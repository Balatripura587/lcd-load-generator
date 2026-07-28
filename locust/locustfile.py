"""LCS Performance Test — Locust entry point.

Endpoint mode controlled by ENDPOINT_TYPE env var:
  query      → POST /v1/query
  streaming  → POST /v1/streaming_query  (SSE with TTFT measurement)

"""

from lib.config import ENDPOINT_TYPE
from lib.metrics import *  

if ENDPOINT_TYPE == "streaming":
    from lib.users import LCSStreamingClient as ActiveUser
else:
    from lib.users import LCSQueryClient as ActiveUser
