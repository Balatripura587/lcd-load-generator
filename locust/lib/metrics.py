import json
import time

from locust import events
from locust.runners import MasterRunner, WorkerRunner

from lib.config import ENDPOINT_TYPE, TEST_UUID
from lib.indexer import index_results
from lib.types import build_result_document

_test_start_time: float = 0.0

_local_ttft_samples: list[float] = []
_local_stream_time_samples: list[float] = []
_local_status_codes: dict[str, int] = {}
_local_bytes_in: list[int] = []
_local_bytes_out: list[int] = []

_aggregated_ttft_samples: list[float] = []
_aggregated_stream_time_samples: list[float] = []
_aggregated_status_codes: dict[str, int] = {}
_aggregated_bytes_in: list[int] = []
_aggregated_bytes_out: list[int] = []


def record_ttft(value: float):
    _local_ttft_samples.append(value)


def record_stream_time(value: float):
    _local_stream_time_samples.append(value)


def record_bytes(bytes_in: int, bytes_out: int):
    _local_bytes_in.append(bytes_in)
    _local_bytes_out.append(bytes_out)


def get_status_codes() -> dict[str, int]:
    return dict(_aggregated_status_codes if _aggregated_status_codes else _local_status_codes)


def get_bytes_stats() -> tuple[float, float]:
    bi = _aggregated_bytes_in if _aggregated_bytes_in else _local_bytes_in
    bo = _aggregated_bytes_out if _aggregated_bytes_out else _local_bytes_out
    avg_in = round(sum(bi) / len(bi), 2) if bi else 0
    avg_out = round(sum(bo) / len(bo), 2) if bo else 0
    return avg_in, avg_out


@events.request.add_listener
def _on_request(request_type, name, response_time, response_length, exception, response=None, **kwargs):
    if exception:
        code = "error"
    elif response is not None and hasattr(response, "status_code"):
        code = str(response.status_code)
    else:
        code = "200"
    _local_status_codes[code] = _local_status_codes.get(code, 0) + 1


@events.report_to_master.add_listener
def _on_report_to_master(client_id, data, **kwargs):
    data["ttft_samples"] = _local_ttft_samples.copy()
    data["stream_time_samples"] = _local_stream_time_samples.copy()
    data["status_codes"] = dict(_local_status_codes)
    data["bytes_in"] = _local_bytes_in.copy()
    data["bytes_out"] = _local_bytes_out.copy()
    _local_ttft_samples.clear()
    _local_stream_time_samples.clear()
    _local_status_codes.clear()
    _local_bytes_in.clear()
    _local_bytes_out.clear()


@events.worker_report.add_listener
def _on_worker_report(client_id, data, **kwargs):
    _aggregated_ttft_samples.extend(data.get("ttft_samples", []))
    _aggregated_stream_time_samples.extend(data.get("stream_time_samples", []))
    _aggregated_bytes_in.extend(data.get("bytes_in", []))
    _aggregated_bytes_out.extend(data.get("bytes_out", []))
    for code, count in data.get("status_codes", {}).items():
        _aggregated_status_codes[code] = _aggregated_status_codes.get(code, 0) + count


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    global _test_start_time
    _test_start_time = time.time()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if isinstance(environment.runner, WorkerRunner):
        return

    test_end_time = time.time()
    stats = environment.stats.total

    if isinstance(environment.runner, MasterRunner):
        ttft = _aggregated_ttft_samples
        stream = _aggregated_stream_time_samples
    else:
        ttft = _local_ttft_samples
        stream = _local_stream_time_samples

    results = build_result_document(
        stats, environment, _test_start_time, test_end_time, ttft, stream,
    )
    print(f"\n[LCS] {ENDPOINT_TYPE} result document:")
    print(json.dumps(results, indent=2))
    index_results(results)
