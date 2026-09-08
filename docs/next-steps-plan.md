# LCS Performance Testing — Next Steps

## Task 1: Elasticsearch Indexing + Grafana Dashboard

### Goal
All load test results and system metrics indexed to ES. Grafana dashboard auto-plots data for analysis.

### Step 1.1: Verify Current ES Indexing

The load generator already indexes two data streams via `py-commons` indexers + kube-burner:

| Data | Index | Indexed By |
|------|-------|------------|
| Locust results (latency, throughput, status codes, TTFT) | `lcs-perf-results` | `py-commons/indexers` (OpenSearchIndexer) |
| Prometheus metrics (CPU, memory, throttling, restarts) | `lcs-perf-metrics` | kube-burner binary |
| Job summary (cluster metadata, test config) | `lcs-perf-results` | `py-commons/indexers` |

Verify indexing works end-to-end:
```bash
# Run a short load test with ES configured
export ES_SERVER=https://<ELASTICSEARCH_HOST>:9200
export ES_INDEX=lcs-perf-results

envsubst < config/lcs-load-generator.yaml | oc apply -f -

# Verify documents landed in ES
curl -sk "$ES_SERVER/lcs-perf-results/_count" | jq .count
curl -sk "$ES_SERVER/lcs-perf-metrics/_count" | jq .count
```

### Step 1.2: Set Up ES Index Templates

Create index templates so new indices get correct mappings and rollover aliases:

```bash
# Create index template for load test results
curl -sk -X PUT "$ES_SERVER/_index_template/lcs-perf-results" \
  -H 'Content-Type: application/json' \
  -d '{
    "index_patterns": ["lcs-perf-results*"],
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1
      },
      "mappings": {
        "properties": {
          "uuid": {"type": "keyword"},
          "timestamp": {"type": "date"},
          "metricName": {"type": "keyword"},
          "workload": {"type": "keyword"},
          "endpoint": {"type": "keyword"},
          "p99Latency": {"type": "long"},
          "p95Latency": {"type": "long"},
          "p50Latency": {"type": "long"},
          "throughput": {"type": "float"},
          "requests": {"type": "integer"},
          "statusCodes": {"type": "object"},
          "clusterVersion": {"type": "keyword"},
          "platform": {"type": "keyword"},
          "workerNodesCount": {"type": "integer"},
          "workerNodesType": {"type": "keyword"}
        }
      }
    }
  }'

# Create index template for Prometheus metrics
curl -sk -X PUT "$ES_SERVER/_index_template/lcs-perf-metrics" \
  -H 'Content-Type: application/json' \
  -d '{
    "index_patterns": ["lcs-perf-metrics*"],
    "template": {
      "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1
      }
    }
  }'
```

### Step 1.3: Set Up ES Rollover Policy

Follow the `cloud-bulldozer/utils/es-reindexer` pattern. This syncs data from the perf-scale ES instance to the internal long-term ES.

**Important naming rule**: Never use an index name that is a prefix of another (e.g., don't use `lcs-perf-results` and `lcs-perf-results-baseline` — use `baseline-lcs-perf-results` instead).

Create a reindexer driver script:

```bash
#!/bin/bash
# File: lcs-perf-results-reindex.sh

export SOURCE_ES="https://<perfscale-es>:9200"
export SOURCE_INDEX="lcs-perf-results"
export DESTINATION_ES="https://<internal-es>:9200"
export DESTINATION_INDEX="lcs-perf-results"
export S3_BUCKET="perfscale-es-backups"
export WEBHOOK_URL="https://hooks.slack.com/services/xxx"  # Slack alerts on failure
export TOUCH_FILE="/var/lib/reindexer/lcs-perf-results.touch"

/opt/es-reindexer/elastic-reindex.sh
```

Set up systemd timer for periodic sync:

```ini
# /etc/systemd/system/lcs-reindex.timer
[Unit]
Description=LCS ES reindex timer

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

First run with `INITIAL_RUN=true` to capture index settings/templates.

### Step 1.4: Create Grafana Dashboard

Create a Grafana dashboard with ES as datasource. Panels needed (from Section 6.3 of test plan):

**Latency panels:**
- p50 / p95 / p99 / max latency — grouped by endpoint, mode, parallel user count
- Streaming TTFT — time-to-first-token for streaming endpoint
- Side-by-side: server vs library mode latency at each concurrency level

**Throughput panels:**
- Requests/sec over time — one line per scenario
- Throughput vs concurrency level comparison

**HTTP status codes:**
- Stacked bar chart — 2xx / 4xx / 5xx distribution per endpoint

**Resource panels:**
- Container CPU usage time series — LCS + Llama Stack containers (server mode), combined container (library mode)
- Container memory RSS time series — server vs library comparison
- CPU throttle ratio — flag if container is being CPU-throttled

**Dashboard variables (dropdowns):**
- `uuid` — select specific test run
- `endpoint` — filter by query/streaming_query
- `mode` — server/library
- `users` — concurrency level (10, 25, 50, 100, 1000)

Export the dashboard JSON and store in the repo at `config/grafana/lcs-perf-dashboard.json`.

### Step 1.5: Verification Checklist

- [ ] Run load test → results appear in ES `lcs-perf-results` index
- [ ] Run load test with `--scrape-metrics` → Prometheus metrics appear in `lcs-perf-metrics` index
- [ ] Grafana dashboard loads and shows data for a given UUID
- [ ] Rollover/reindexer syncs data to internal ES
- [ ] Slack alert fires if reindexer hasn't run in 14 days

---

## Task 2: Orion Change Point Detection

### Goal
Run Orion against LCS load test data in ES to detect performance regressions. Get notified when changepoints are detected.

### Step 2.1: Create Orion Config for LCS

Create an Orion config file that defines the metrics to monitor for changepoints.

```yaml
# File: orion-config/lcs-load-generator.yaml
tests:
  - name: lcs-load-test-post-query
    index: lcs-perf-results*
    benchmarkIndex: lcs-perf-results*
    metadata:
      ocpVersion: clusterVersion
      platform: platform
    metrics:
      - name: p99Latency-post-query
        metricName: post_query
        metric_of_interest: p99Latency
        agg:
          value: avg
          agg_type: avg
      - name: p95Latency-post-query
        metricName: post_query
        metric_of_interest: p95Latency
        agg:
          value: avg
          agg_type: avg
      - name: throughput-post-query
        metricName: post_query
        metric_of_interest: throughput
        agg:
          value: avg
          agg_type: avg
      - name: avgLatency-post-query
        metricName: post_query
        metric_of_interest: reqLatency
        agg:
          value: avg
          agg_type: avg

  - name: lcs-load-test-streaming-query
    index: lcs-perf-results*
    benchmarkIndex: lcs-perf-results*
    metrics:
      - name: p99Latency-streaming-query
        metricName: post_streaming_query
        metric_of_interest: p99Latency
        agg:
          value: avg
          agg_type: avg
      - name: ttft-streaming-query
        metricName: post_streaming_query
        metric_of_interest: ttft
        agg:
          value: avg
          agg_type: avg
      - name: throughput-streaming-query
        metricName: post_streaming_query
        metric_of_interest: throughput
        agg:
          value: avg
          agg_type: avg
```

### Step 2.2: Run Orion Locally to Validate

```bash
# Install Orion
pip install orion-runner

# Run changepoint detection
orion cmd \
  --config orion-config/lcs-load-generator.yaml \
  --lookback 180 \
  --hunter-analyze \
  --output json \
  --es-url $ES_SERVER

# Or with JUNIT output (used in CI)
orion cmd \
  --config orion-config/lcs-load-generator.yaml \
  --lookback 180 \
  --lookback-size 15 \
  --hunter-analyze \
  --output junit \
  --es-url $ES_SERVER \
  > junit-orion.xml
```

### Step 2.3: Create ACK File for Known Regressions

When a known regression is detected (e.g., expected after an LCS version bump), acknowledge it so Orion doesn't keep flagging it:

```yaml
# File: orion-config/ack/all_ack.yaml
# Acknowledge known changepoints to suppress alerts
- uuid: "<test-run-uuid>"
  metric: "p99Latency-post-query"
  reason: "Expected regression after LCS 0.7.0 upgrade"
```

### Step 2.4: Critical KPIs for Change Point Detection

Based on the test plan Section 6.2 (Pass/Fail Criteria):

| KPI | Why Monitor |
|-----|-------------|
| p99 latency (query) | Primary latency indicator — regression = perf bug |
| p99 latency (streaming_query) | Streaming path may regress independently |
| Throughput (query) | Drop = capacity regression |
| TTFT (streaming) | Streaming responsiveness — user-facing metric |
| Error rate (5xx) | Spike = stability regression |
| Memory RSS (20-min run) | Growth = potential memory leak |
| CPU at steady state (25 users) | Spike = code efficiency regression |

### Step 2.5: Verification Checklist

- [ ] Orion config loads without errors
- [ ] `orion cmd` runs and returns results against ES data
- [ ] JUNIT output generates valid XML (for CI integration)
- [ ] ACK file suppresses known changepoints
- [ ] At least 15 runs worth of data in ES before meaningful changepoint detection

---

## Task 3: CPT Pipeline in OpenShift CI (Prow)

### Goal
Automated periodic load tests running in OpenShift CI, indexing to ES, running Orion, and notifying via Slack.

### Step 3.1: Create Prow Dockerfile

The CI builds and promotes the load generator image. Create a Prow-specific Dockerfile:

```dockerfile
# File: prow/Dockerfile
FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0

RUN dnf install -y jq tar gzip && dnf clean all

# kube-burner v1.10.4
RUN curl -sL https://github.com/kube-burner/kube-burner/releases/download/v1.10.4/kube-burner-V1.10.4-linux-x86_64.tar.gz \
    | tar xz -C /usr/local/bin/ kube-burner

WORKDIR /opt/lcs-load-generator

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "rh-py-commons[ocp_metadata,indexers] @ git+https://github.com/cloud-bulldozer/py-commons.git"

COPY locust/ ./locust/
COPY lcs-load-generator ./
COPY config/ ./config/
COPY orion-config/ ./orion-config/
RUN chmod +x lcs-load-generator

USER 1001

ENTRYPOINT ["python3", "./lcs-load-generator"]
CMD ["run"]
```

### Step 3.2: Create CI Test Step Ref

This is the step that runs in Prow after the cluster is provisioned. Create the ref script:

```bash
# File: prow/lcs-load-generator-tests.sh
#!/bin/bash
set -euo pipefail

# Read ES credentials from mounted secret
ES_SERVER=$(cat /secret/es_server)
ES_INDEX="lcs-perf-results"
ES_INDEX_METRICS="lcs-perf-metrics"

# Deploy LCS (lightspeed-stack) on the cluster
# Using lightspeed-stack Helm chart or manifests
oc new-project lightspeed-stack-server
# ... deploy LCS with fake LLM provider (mock OpenAI server as sidecar)

# Wait for LCS to be ready
oc rollout status deployment/lightspeed-stack-server -n lightspeed-stack-server --timeout=300s
LCS_HOST="https://$(oc get route lightspeed-stack-server -n lightspeed-stack-server -o jsonpath='{.spec.host}')"

# Deploy load generator as a Job
export LCS_NAMESPACE=lightspeed-stack-server
export LCS_LOADGEN_IMAGE="${IMAGE_LCS_LOAD_GENERATOR}"  # Promoted image from CI
export LCS_HOST
export LCS_TOKEN=$(oc create token default -n lightspeed-stack-server)
export LCS_PROVIDER=openai
export LCS_MODEL=fake-model
export LOCUST_USERS="${LOCUST_USERS:-25}"
export LOCUST_RUN_TIME="${LOCUST_RUN_TIME:-5m}"
export LOCUST_PROCESSES="${LOCUST_PROCESSES:-1}"
export REQUEST_TIMEOUT=120
export ES_SERVER
export ES_INDEX
export METRIC_STEP=30

envsubst < config/lcs-load-generator.yaml | oc apply -f -

# Wait for Job to complete
oc wait --for=condition=complete job/lcs-load-generator \
  -n lcs-perf-testing --timeout=1800s || true

# Capture logs as artifacts
oc logs job/lcs-load-generator -n lcs-perf-testing > "${ARTIFACT_DIR}/load-generator.log" 2>&1

# Index fingerprint (CI metadata) to ES for Orion correlation
UUID=$(grep -oP '"uuid":\s*"\K[^"]+' "${ARTIFACT_DIR}/load-generator.log" | head -1)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

curl -sk -X POST "$ES_SERVER/perf_scale_ci/_doc" \
  -H 'Content-Type: application/json' \
  -d "{
    \"uuid\": \"${UUID}\",
    \"timestamp\": \"${TIMESTAMP}\",
    \"buildUrl\": \"${BUILD_URL:-}\",
    \"ciSystem\": \"prow\",
    \"jobName\": \"${JOB_NAME:-lcs-load-generator}\",
    \"nodeName\": \"${NODE_NAME:-}\",
    \"clusterType\": \"aws\",
    \"upstreamJob\": \"${JOB_NAME:-}\",
    \"upstreamJobBuild\": \"${BUILD_ID:-}\"
  }"

echo "Test UUID: ${UUID}"
echo "Results indexed to: ${ES_SERVER}/${ES_INDEX}"
```

### Step 3.3: Create ci-operator Config

Following the OLS load generator pattern:

```yaml
# File: ci-operator/config/lightspeed-core/lcs-load-generator/
#       lightspeed-core-lcs-load-generator-main.yaml

build_root:
  image_stream_tag:
    name: release
    namespace: openshift
    tag: rhel-9-release-golang-1.25-openshift-4.22

images:
  - dockerfile_path: prow/Dockerfile
    to: lcs-load-generator

promotion:
  to:
    - namespace: lcs-load-generator
      tag: latest

releases:
  latest:
    candidate:
      product: ocp
      stream: nightly
      version: "4.22"

tests:
  # 10 users — low concurrency baseline
  - as: lcs-load-test-10users
    cron: 0 0 * * 1    # Monday midnight
    steps:
      cluster_profile: aws-perfscale-qe
      env:
        COMPUTE_NODE_TYPE: m6i.xlarge
        COMPUTE_NODE_COUNT: "3"
        LOCUST_USERS: "10"
        LOCUST_RUN_TIME: "5m"
      test:
        - ref: lcs-load-generator-tests
        - ref: openshift-qe-orion
          env:
            ORION_CONFIG: orion-config/lcs-load-generator.yaml
            ES_BENCHMARK_INDEX: lcs-perf-results*
            ES_METADATA_INDEX: perf_scale_ci*
            LOOKBACK: "180"
            LOOKBACK_SIZE: "15"
      workflow: openshift-qe-installer-aws
    reporter_config:
      slack:
        channel: '#lcs-perf-scale-ci-results'
        report_template: >-
          {{if eq .Status.State "success"}} :white_check_mark:
          {{else}} :warning: {{end}}
          Job *{{.Spec.Job}}* ended with *{{.Status.State}}*.
          <{{.Status.URL}}|View logs>

  # 25 users — moderate concurrency
  - as: lcs-load-test-25users
    cron: 0 6 * * 1    # Monday 6am
    steps:
      cluster_profile: aws-perfscale-qe
      env:
        COMPUTE_NODE_TYPE: m6i.xlarge
        COMPUTE_NODE_COUNT: "3"
        LOCUST_USERS: "25"
        LOCUST_RUN_TIME: "10m"
      test:
        - ref: lcs-load-generator-tests
        - ref: openshift-qe-orion
          env:
            ORION_CONFIG: orion-config/lcs-load-generator.yaml
            ES_BENCHMARK_INDEX: lcs-perf-results*
            ES_METADATA_INDEX: perf_scale_ci*
            LOOKBACK: "180"
            LOOKBACK_SIZE: "15"
      workflow: openshift-qe-installer-aws
    reporter_config:
      slack:
        channel: '#lcs-perf-scale-ci-results'
        report_template: >-
          {{if eq .Status.State "success"}} :white_check_mark:
          {{else}} :warning: {{end}}
          Job *{{.Spec.Job}}* ended with *{{.Status.State}}*.
          <{{.Status.URL}}|View logs>

  # 50 users — high concurrency
  - as: lcs-load-test-50users
    cron: 0 12 * * 1   # Monday noon
    steps:
      cluster_profile: aws-perfscale-qe
      env:
        COMPUTE_NODE_TYPE: m6i.xlarge
        COMPUTE_NODE_COUNT: "3"
        LOCUST_USERS: "50"
        LOCUST_RUN_TIME: "10m"
      test:
        - ref: lcs-load-generator-tests
        - ref: openshift-qe-orion
          env:
            ORION_CONFIG: orion-config/lcs-load-generator.yaml
            ES_BENCHMARK_INDEX: lcs-perf-results*
            ES_METADATA_INDEX: perf_scale_ci*
            LOOKBACK: "180"
            LOOKBACK_SIZE: "15"
      workflow: openshift-qe-installer-aws
    reporter_config:
      slack:
        channel: '#lcs-perf-scale-ci-results'
        report_template: >-
          {{if eq .Status.State "success"}} :white_check_mark:
          {{else}} :warning: {{end}}
          Job *{{.Spec.Job}}* ended with *{{.Status.State}}*.
          <{{.Status.URL}}|View logs>

  # 100 users — stress test
  - as: lcs-load-test-100users
    cron: 0 18 * * 1   # Monday 6pm
    steps:
      cluster_profile: aws-perfscale-qe
      env:
        COMPUTE_NODE_TYPE: m6i.xlarge
        COMPUTE_NODE_COUNT: "3"
        LOCUST_USERS: "100"
        LOCUST_RUN_TIME: "10m"
      test:
        - ref: lcs-load-generator-tests
        - ref: openshift-qe-orion
          env:
            ORION_CONFIG: orion-config/lcs-load-generator.yaml
            ES_BENCHMARK_INDEX: lcs-perf-results*
            ES_METADATA_INDEX: perf_scale_ci*
            LOOKBACK: "180"
            LOOKBACK_SIZE: "15"
      workflow: openshift-qe-installer-aws
    reporter_config:
      slack:
        channel: '#lcs-perf-scale-ci-results'
        report_template: >-
          {{if eq .Status.State "success"}} :white_check_mark:
          {{else}} :warning: {{end}}
          Job *{{.Spec.Job}}* ended with *{{.Status.State}}*.
          <{{.Status.URL}}|View logs>
```

### Step 3.4: Create Slack Channel

- Create `#lcs-perf-scale-ci-results` Slack channel
- Add Prow bot integration
- Add team members for notifications

### Step 3.5: Submit PRs

Three PRs needed:

1. **`lightspeed-core/lcs-load-generator`** — Add `prow/Dockerfile`, `orion-config/`, `prow/lcs-load-generator-tests.sh`
2. **`openshift/release`** — Add ci-operator config at `ci-operator/config/lightspeed-core/lcs-load-generator/` and step ref at `ci-operator/step-registry/lcs/load-generator/`
3. **`cloud-bulldozer/utils`** — Add ES reindexer driver script + systemd timer for `lcs-perf-results`

### Step 3.6: Pipeline Flow (End-to-End)

```
Prow cron trigger (weekly per concurrency level)
  │
  ├── 1. Provision AWS cluster (openshift-qe-installer-aws)
  │
  ├── 2. Build + promote lcs-load-generator image
  │
  ├── 3. Deploy LCS + mock LLM on cluster
  │
  ├── 4. Run load test (lcs-load-generator Job)
  │     ├── Locust results → ES (lcs-perf-results)
  │     └── Prometheus metrics → ES (lcs-perf-metrics) via kube-burner
  │
  ├── 5. Index CI fingerprint → ES (perf_scale_ci)
  │
  ├── 6. Orion changepoint detection (openshift-qe-orion)
  │     ├── Query ES for last 15 runs over 180 days
  │     ├── Detect regressions in p99, throughput, TTFT
  │     └── Output JUNIT XML (fail = regression detected)
  │
  ├── 7. Tear down cluster
  │
  └── 8. Slack notification → #lcs-perf-scale-ci-results
        ├── ✅ success — no regressions
        └── ⚠️ failure — regression detected or test failed
```

### Step 3.7: Verification Checklist

- [ ] CI config passes `make ci-operator-config` validation
- [ ] Prow job triggers on schedule
- [ ] Cluster provisions → LCS deploys → load test runs → indexes to ES
- [ ] Orion step runs and generates JUNIT output
- [ ] Slack notification fires on success and failure
- [ ] ES reindexer syncs data to internal ES on schedule
- [ ] Grafana dashboard shows accumulating data across runs

---

## Task 4: CPU + Memory Profiling (Pyroscope + Memray)

### Goal

Capture CPU flamegraphs and heap allocation profiles from LCS under real load, to identify code-level bottlenecks that Prometheus metrics alone cannot explain.

### Background

Prometheus tells us **what** is happening (CPU at 90%, p99 at 8s). Profiling tells us **where** in code — which function, which call stack. LCS does not expose pprof-style endpoints today, so we use Pyroscope (CPU) and Memray (memory) instead.

The LCS dev team was asked to expose profiling endpoints. They confirmed none exist. Two paths forward:
- **If they expose endpoints** — hit `/debug/profile/cpu` and `/debug/profile/memory` directly during load test. No extra tooling needed.
- **If they don't expose endpoints** — use Pyroscope SDK (CPU) + Memray (memory), as documented below.

### Step 4.1: Code Changes in LCS repo

These changes were made to `lightspeed-core/lightspeed-stack`:

**`pyproject.toml`** — add as dev dependencies (not shipped to customers):
```toml
[dependency-groups]
dev = [
    ...existing...
    "pyroscope-io>=0.8.7",
    "memray>=1.13.0",
]
```

**`src/profiling.py`** (new file, follows `sentry.py` pattern):
```python
"""Pyroscope CPU profiling initialization for Lightspeed Core Stack."""

import os
from log import get_logger

logger = get_logger(__name__)
PYROSCOPE_SERVER_ADDRESS_ENV_VAR = "PYROSCOPE_SERVER_ADDRESS"

def initialize_pyroscope() -> None:
    """Initialize Pyroscope continuous CPU profiling.

    Reads PYROSCOPE_SERVER_ADDRESS env var. Zero overhead when not set.
    ~3-5% CPU overhead when active.
    """
    server_address = os.environ.get(PYROSCOPE_SERVER_ADDRESS_ENV_VAR)
    if not server_address:
        logger.debug("Pyroscope profiling disabled (%s not set)", PYROSCOPE_SERVER_ADDRESS_ENV_VAR)
        return
    try:
        import pyroscope  # pyright: ignore[reportMissingImports]
        pyroscope.configure(
            application_name="lightspeed-stack",
            server_address=server_address,
        )
        logger.info("Pyroscope CPU profiling enabled, pushing to %s", server_address)
    except ImportError:
        logger.warning("pyroscope-io not installed; install dev dependencies to enable CPU profiling")
```

**`src/app/main.py`** — call alongside `initialize_sentry()`:
```python
from profiling import initialize_pyroscope

async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configuration.load_configuration(...)
    initialize_sentry()
    initialize_pyroscope()   # added
    ...
```

**`scripts/entrypoint.sh`** — add Memray gate alongside existing OTel gate:
```bash
#!/bin/bash
set -e

if [ "${OTEL_SDK_DISABLED:-true}" = "false" ]; then
    PYTHON_CMD="/app-root/.venv/bin/opentelemetry-instrument /app-root/.venv/bin/python"
else
    PYTHON_CMD="/app-root/.venv/bin/python"
fi

# Memray: only active when ENABLE_MEMRAY=true. Dedicated single-request run only.
# Generate flamegraph: python -m memray flamegraph /tmp/memray-lcs.bin
if [ "${ENABLE_MEMRAY:-false}" = "true" ]; then
    exec /app-root/.venv/bin/python -m memray run \
        -o /tmp/memray-lcs.bin \
        src/lightspeed_stack.py "$@"
else
    exec ${PYTHON_CMD} src/lightspeed_stack.py "$@"
fi
```

Both are fully optional:
- Pyroscope: zero overhead when `PYROSCOPE_SERVER_ADDRESS` not set
- Memray: only active when `ENABLE_MEMRAY=true`, requires pod restart

### Step 4.2: Build Perf Image

The published `quay.io/lightspeed-core/lightspeed-stack:dev-latest` image does not include dev dependencies. Create a perf-specific image that extends it:

```dockerfile
# File: prow/Containerfile.lcs-perf
FROM quay.io/lightspeed-core/lightspeed-stack:dev-latest

USER 0
RUN pip install pyroscope-io memray
USER 1001
```

Build and push to our perf quay namespace:
```bash
podman build -f prow/Containerfile.lcs-perf \
  -t quay.io/redhat-perf/lightspeed-stack-perf:latest .
podman push quay.io/redhat-perf/lightspeed-stack-perf:latest
```

This is the image used in all perf test deployments. Normal LCS customers use the upstream image — unaffected.

### Step 4.3: Deploy Pyroscope Server on Cluster

Deploy Pyroscope server in its own namespace on the perf cluster:

```bash
oc new-project pyroscope
# Apply Vishnu's yaml (tested with OLS, same cluster setup)
oc apply -f <pyroscope-cluster-yaml>

# Verify it is running
oc get pods -n pyroscope
oc get svc -n pyroscope   # should show pyroscope service on port 4040
```

Pyroscope UI is accessible at the route — view flamegraphs there after load test runs.

### Step 4.4: Enable Pyroscope in CI Step Script (CPU Profiling)

In `prow/lcs-load-generator-tests-commands.sh`, deploy LCS using the perf image and set the env var:

```bash
LCS_IMAGE="quay.io/redhat-perf/lightspeed-stack-perf:latest"

oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lcs
  namespace: openshift-lcs
spec:
  replicas: 1
  selector:
    matchLabels:
      app: lcs
  template:
    metadata:
      labels:
        app: lcs
    spec:
      containers:
      - name: lcs
        image: ${LCS_IMAGE}
        env:
        - name: PYROSCOPE_SERVER_ADDRESS
          value: "http://pyroscope.pyroscope.svc:4040"
        ...rest of container spec...
EOF

oc wait --for=condition=Available deployment/lcs -n openshift-lcs --timeout=300s
```

Pyroscope automatically collects CPU flamegraphs from LCS startup through the entire load test run. View in Pyroscope UI correlated by time against the load test window.

### Step 4.5: Enable Memray After Load Test (Memory Profiling)

Memray has ~50-100% overhead — cannot run during load test. Run it as a dedicated single-request session after the load test completes, before cleanup:

```bash
# Restart LCS with Memray enabled
oc set env deployment/lcs ENABLE_MEMRAY=true -n openshift-lcs
oc rollout status deployment/lcs -n openshift-lcs --timeout=120s

# Send a few single requests (not full load)
LCS_TOKEN=$(oc create token default -n openshift-lcs --duration=3600s)
for i in 1 2 3; do
  curl -sk -X POST https://lcs.openshift-lcs.svc:8080/v1/query \
    -H "Authorization: Bearer ${LCS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"what is openshift?\", \"conversation_id\": \"memray-${i}\"}"
done

# Generate flamegraph inside the pod
LCS_POD=$(oc get pod -n openshift-lcs -l app=lcs -o jsonpath='{.items[0].metadata.name}')
oc exec "${LCS_POD}" -n openshift-lcs -- \
  python -m memray flamegraph /tmp/memray-lcs.bin -o /tmp/memray.html

# Copy to CI artifacts
oc cp "openshift-lcs/${LCS_POD}:/tmp/memray.html" \
  "${ARTIFACT_DIR}/memray-flamegraph.html"

# Restore LCS to normal before cluster teardown
oc set env deployment/lcs ENABLE_MEMRAY- -n openshift-lcs
```

The flamegraph HTML is stored in `ARTIFACT_DIR` and accessible from the Prow job artifacts page.

### Step 4.6: Comparison — pprof endpoints vs Pyroscope+Memray

| | pprof endpoints (if LCS exposes) | Pyroscope + Memray (fallback) |
|---|---|---|
| CPU profiling | `curl /debug/profile/cpu` during load test | Pyroscope SDK, continuous, ~3-5% overhead |
| Memory profiling | `curl /debug/profile/memory` during load test | Memray, dedicated run, ~50-100% overhead |
| Overhead | Near-zero, on-demand | Pyroscope: low; Memray: high |
| Run alongside load test | Yes, both | Pyroscope: yes; Memray: no |
| Code changes needed | ~25 lines (FastAPI endpoint) | pyproject.toml + profiling.py + entrypoint.sh |
| Extra infra | None | Pyroscope server on cluster |

### Step 4.7: Verification Checklist

- [ ] LCS code changes merged (profiling.py, main.py, entrypoint.sh, pyproject.toml)
- [ ] Perf image built and pushed to `quay.io/redhat-perf/lightspeed-stack-perf:latest`
- [ ] Pyroscope server deployed and healthy on perf cluster
- [ ] LCS starts with `PYROSCOPE_SERVER_ADDRESS` set — logs show "Pyroscope CPU profiling enabled"
- [ ] Load test runs — CPU flamegraphs appear in Pyroscope UI during test window
- [ ] Memray run completes — `memray-flamegraph.html` appears in Prow artifacts
- [ ] Normal LCS deployment (without env vars) shows no profiling overhead

---

## Implementation Order

| Order | Task | Dependency |
|-------|------|------------|
| 1 | Verify ES indexing works (Task 1.1) | Load generator PR merged |
| 2 | Create ES index templates (Task 1.2) | ES access |
| 3 | Create Orion config + validate locally (Task 2.1-2.2) | ~15 runs of data in ES |
| 4 | Create Grafana dashboard (Task 1.4) | ES data available |
| 5 | Create Prow Dockerfile + test ref (Task 3.1-3.2) | Load generator repo stable |
| 6 | Submit ci-operator config PR (Task 3.3) | Test ref ready |
| 7 | Set up ES reindexer (Task 1.3) | CI running and indexing |
| 8 | Set up Slack channel + notifications (Task 3.4) | CI config merged |
| 9 | LCS code changes for profiling (Task 4.1) | LCS team agreement |
| 10 | Build + push perf image (Task 4.2) | Task 4.1 merged |
| 11 | Deploy Pyroscope server on perf cluster (Task 4.3) | Perf cluster access |
| 12 | Enable profiling in CI step script (Task 4.4-4.5) | Tasks 4.2 + 4.3 done |
