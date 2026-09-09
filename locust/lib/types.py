"""Result document builder for per-endpoint load test metrics."""

import platform
import time

from lib.config import ENDPOINT_TYPE, LCS_MODEL, LCS_PROVIDER, REQUEST_TIMEOUT, TEST_UUID


def _percentiles(samples, prefix):
    """Compute p50, p95, p99, and avg from a list of numeric samples."""
    if not samples:
        return {}
    sorted_s = sorted(samples)
    n = len(sorted_s)
    return {
        f"{prefix}_p50": round(sorted_s[int(n * 0.50)], 2),
        f"{prefix}_p95": round(sorted_s[int(n * 0.95)], 2),
        f"{prefix}_p99": round(sorted_s[min(int(n * 0.99), n - 1)], 2),
        f"{prefix}_avg": round(sum(sorted_s) / n, 2),
    }


def _status_codes(stats, environment):
    """Return HTTP status code distribution from custom tracking or Locust stats."""
    from lib.metrics import get_status_codes
    codes = get_status_codes()
    if not codes:
        ok = stats.num_requests - stats.num_failures
        if ok > 0:
            codes["200"] = ok
        if stats.num_failures > 0:
            codes["error"] = stats.num_failures
    return codes


METRIC_NAMES = {
    "query": "post_query",
    "streaming": "post_streaming_query",
    "responses": "post_responses",
    "streaming_responses": "post_streaming_responses",
}


def build_result_document(
    stats,
    environment,
    start_time: float,
    end_time: float,
    ttft_samples: list[float],
    stream_time_samples: list[float],
) -> dict:
    """Assemble a per-endpoint result document with latency, throughput, and TTFT stats."""
    elapsed = end_time - start_time
    user_count = environment.runner.target_user_count if environment.runner else 0

    results = {
        "workload": "lcs-locust",
        "endpoint": environment.host or "",
        "requestTimeout": REQUEST_TIMEOUT,
        "metricName": METRIC_NAMES.get(ENDPOINT_TYPE, "post_query"),
        "hostname": platform.node(),
        "duration": f"{int(elapsed)}s",
        "users": user_count,
        "throughput": round(stats.total_rps, 4),
        "statusCodes": _status_codes(stats, environment),
        "requests": stats.num_requests,
        "p99Latency": round(stats.get_response_time_percentile(0.99) or 0, 2),
        "p95Latency": round(stats.get_response_time_percentile(0.95) or 0, 2),
        "p50Latency": round(stats.get_response_time_percentile(0.50) or 0, 2),
        "maxLatency": round(stats.max_response_time or 0, 2),
        "minLatency": round(stats.min_response_time or 0, 2),
        "avgLatency": round(stats.avg_response_time or 0, 2),
        "bytesIn": 0,
        "bytesOut": 0,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(start_time)),
        "endTimestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(end_time)),
        "elapsedTime": round(elapsed, 2),
        "uuid": TEST_UUID,
    }

    from lib.metrics import get_bytes_stats
    avg_in, avg_out = get_bytes_stats()
    results["bytesIn"] = avg_in
    results["bytesOut"] = avg_out

    if ENDPOINT_TYPE in ("streaming", "streaming_responses"):
        results.update(_percentiles(ttft_samples, "ttft"))
    results.update(_percentiles(stream_time_samples, "streamTime"))

    return results
