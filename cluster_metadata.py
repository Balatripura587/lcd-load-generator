import logging
import subprocess

logger = logging.getLogger(__name__)


def _run(cmd: str) -> str:
    try:
        logger.debug("Running: %s", cmd)
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        value = result.stdout.strip().strip("'\"")
        if result.returncode != 0:
            logger.debug("Command failed (rc=%d): %s", result.returncode, result.stderr.strip())
        else:
            logger.debug("Result: %s", value)
        return value
    except Exception as e:
        logger.debug("Command exception: %s", e)
        return ""


def _get_worker_resources() -> tuple[int, int]:
    cpu_str = _run(
        "oc get nodes -l node-role.kubernetes.io/worker "
        "-o jsonpath='{range .items[*]}{.status.capacity.cpu}{\" \"}{end}'"
    )
    mem_str = _run(
        "oc get nodes -l node-role.kubernetes.io/worker "
        "-o jsonpath='{range .items[*]}{.status.capacity.memory}{\" \"}{end}'"
    )
    total_cpu = 0
    for v in cpu_str.split():
        try:
            total_cpu += int(v)
        except ValueError:
            pass

    total_mem_ki = 0
    for v in mem_str.split():
        v = v.rstrip("Ki")
        try:
            total_mem_ki += int(v)
        except ValueError:
            pass

    return total_cpu, total_mem_ki


def get_cluster_metadata() -> dict:
    """Collect OCP cluster metadata matching go-commons ocp-metadata fields."""
    metadata = {}

    metadata["clusterVersion"] = _run(
        "oc get clusterversion version -o jsonpath='{.status.desired.version}'"
    )
    metadata["platform"] = _run(
        "oc get infrastructure cluster -o jsonpath='{.status.platform}'"
    )
    metadata["clusterName"] = _run(
        "oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}'"
    )
    worker_count = _run(
        "oc get nodes -l node-role.kubernetes.io/worker --no-headers 2>/dev/null | wc -l"
    )
    metadata["workerNodesCount"] = int(worker_count) if worker_count.isdigit() else 0
    metadata["sdnType"] = _run(
        "oc get network.config cluster -o jsonpath='{.status.networkType}'"
    )

    total_cpu, total_mem_ki = _get_worker_resources()
    metadata["totalWorkerCPU"] = total_cpu
    metadata["totalWorkerMemoryKi"] = total_mem_ki

    if not metadata["clusterVersion"]:
        logger.warning("Could not collect OCP metadata — oc may not be available or not logged in")
    else:
        logger.debug("Collected metadata: cluster=%s platform=%s workers=%d",
                      metadata["clusterVersion"], metadata["platform"], metadata["workerNodesCount"])

    return metadata
