"""OCP cluster metadata collection and Prometheus discovery.

Mirrors go-commons ocp-metadata package. Collects cluster information via
``oc`` CLI commands and discovers the Prometheus/Thanos endpoint for tools
that need direct metric access.
"""

import logging
import subprocess

logger = logging.getLogger("py_commons.ocp_metadata")


def _run(cmd: str) -> str:
    """Run a shell command and return stripped stdout."""
    try:
        logger.debug("Running: %s", cmd)
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
        value = result.stdout.strip().strip("'\"")
        if result.returncode != 0:
            logger.debug("Command failed (rc=%d): %s",
                         result.returncode, result.stderr.strip())
        else:
            logger.debug("Result: %s", value)
        return value
    except Exception as e:
        logger.debug("Command exception: %s", e)
        return ""


def _count_nodes(label: str) -> int:
    """Count nodes matching a role label."""
    count = _run(
        f"oc get nodes -l node-role.kubernetes.io/{label} "
        f"--no-headers 2>/dev/null | wc -l"
    )
    return int(count) if count.strip().isdigit() else 0


def _get_worker_resources() -> tuple[int, int]:
    """Sum CPU cores and memory (Ki) across worker nodes."""
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
    """Collect OCP cluster metadata matching go-commons ClusterMetadata fields."""
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
    metadata["sdnType"] = _run(
        "oc get network.config cluster -o jsonpath='{.status.networkType}'"
    )

    metadata["masterNodesCount"] = _count_nodes("master")
    metadata["workerNodesCount"] = _count_nodes("worker")
    metadata["infraNodesCount"] = _count_nodes("infra")
    metadata["totalNodes"] = int(
        _run("oc get nodes --no-headers 2>/dev/null | wc -l") or 0
    )

    metadata["workerNodesType"] = _run(
        "oc get machines -n openshift-machine-api "
        "-l machine.openshift.io/cluster-api-machine-role=worker "
        "-o jsonpath='{.items[0].spec.providerSpec.value.instanceType}'"
    )

    metadata["region"] = _run(
        "oc get infrastructure cluster "
        "-o jsonpath='{.status.platformStatus.aws.region}'"
    )
    if not metadata["region"]:
        metadata["region"] = _run(
            "oc get infrastructure cluster "
            "-o jsonpath='{.status.platformStatus.gcp.region}'"
        )

    total_cpu, total_mem_ki = _get_worker_resources()
    metadata["totalWorkerCPU"] = total_cpu
    metadata["totalWorkerMemoryKi"] = total_mem_ki

    if not metadata["clusterVersion"]:
        logger.warning(
            "Could not collect OCP metadata — oc may not be available or not logged in"
        )
    else:
        logger.debug(
            "Collected metadata: cluster=%s platform=%s workers=%d",
            metadata["clusterName"], metadata["platform"],
            metadata["workerNodesCount"],
        )

    return metadata


def get_prometheus(sa_name="lcs-locust-sa", namespace="lcs-perf-testing") -> tuple[str, str]:
    """Discover the Prometheus/Thanos endpoint and obtain a bearer token.

    Returns:
        (prometheus_url, bearer_token)

    Raises:
        RuntimeError: if the Thanos route cannot be discovered.
    """
    host = _run(
        "oc get route thanos-querier -n openshift-monitoring "
        "-o jsonpath='{.spec.host}'"
    )
    if not host:
        raise RuntimeError("Could not discover thanos-querier route")

    prometheus_url = f"https://{host}"
    logger.info("Discovered Prometheus endpoint: %s", prometheus_url)

    token = _run(
        f"oc create token {sa_name} -n {namespace} --duration=1h"
    )
    if not token:
        logger.debug("SA token failed, falling back to oc whoami -t")
        token = _run("oc whoami -t")

    if not token:
        raise RuntimeError("Could not obtain bearer token for Prometheus")

    logger.debug("Obtained Prometheus bearer token")
    return prometheus_url, token
