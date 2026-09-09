# LCS Performance Testing — Next Steps Plan

## Status Summary

| Task | Status |
|------|--------|
| ES indexing — Locust results | ✅ Done |
| ES indexing — Prometheus metrics (kube-burner) | ✅ Done |
| Grafana dashboards (dittybopper) | ✅ Done |
| Pyroscope CPU profiling integration in LCS | ✅ Done (PR open) |
| Memray memory profiling (command override) | ✅ Done |
| Local profiling collection (`collect-profiles.sh`) | ✅ Done |
| Orion changepoint detection config | ⏳ Pending |
| CI pipeline (Prow periodic jobs) | ⏳ Pending |
| ES reindexer (perf-scale → internal ES) | ⏳ Pending |

---

## What We Have Now

### Architecture

```
Load Generator Job (Locust)
  → POST /v1/query, /v1/streaming_query
  → LCS (library mode) + Mock LLM sidecar (localhost:11434)
  → Results indexed to ES: lcs-perf-results
  → Prometheus metrics (kube-burner): lcs-perf-results (same index, different metricName)

Optional profiling:
  → Pyroscope SDK in LCS → Grafana Pyroscope server (CPU flamegraphs)
  → Memray command override (memory flamegraphs, dedicated runs)
```

### Repository Ownership — What Goes Where

**`lightspeed-core/lcs-load-generator`** — generic, reusable by anyone:
```
Containerfile                          # load generator image
lcs-load-generator                     # CLI tool (Locust + kube-burner)
locust/                                # test logic, questions, metrics profiles
config/lcs-load-generator.yaml         # OpenShift Job spec (env vars only)
config/monitoring/platform-prometheus.yaml  # ServiceMonitor
config/grafana/lcs-perf-results-dashboard.json
orion-config/lcs-load-generator.yaml   # Orion changepoint config
```

**`openshift/release` step registry** — CI-specific, all LCS deployment config lives here:
```
ci-operator/step-registry/lcs/load-generator/tests/
  lcs-load-generator-tests-ref.yaml        # step ref
  lcs-load-generator-tests-commands.sh     # deploys LCS inline (no external files)
  Containerfile.lcs-perf                   # LCS overlay image (dev-latest + profiling)
```

The commands script is **self-contained** — it embeds all LCS deployment config inline (lightspeed-stack.yaml, run.yaml, deployment YAML as heredocs). No external files needed from the load generator repo for LCS deployment.

### ES Index Structure

All data goes to `lcs-perf-results`. Filter by `metricName`:

| `metricName` | Source | Fields |
|---|---|---|
| `post_query` | Locust | p50/p95/p99Latency, throughput, requests, users |
| `post_streaming_query` | Locust | + ttft_p50/p95/p99, streamTime_p50/p95/p99 |
| `jobSummary` | py-commons | clusterVersion, platform, workerNodesCount |
| `containerCPU` | kube-burner | value (time-series CPU) |
| `containerMemory` | kube-burner | value (time-series memory) |
| `avg-cpu-lcs` / `max-cpu-lcs` | kube-burner | value (aggregate CPU) |
| `avg-memory-lcs` / `max-memory-lcs` | kube-burner | value (aggregate memory) |
| `max-cpu-throttled-lcs` | kube-burner | value |
| `pod-restarts` | kube-burner | value |

### Known Issues / Fixed

- kube-burner `--metrics-profile` requires relative path + `cwd=results_dir` (absolute paths trigger HTTP GET)
- `ogx:` is the correct key in `lightspeed-stack.yaml` (`llama_stack:` deprecated but still works)
- Memray uses command override (not entrypoint.sh), `--aggregate` flag keeps data in memory — use streaming mode (no `--aggregate`) for live servers
- Pyroscope needs `grafana/pyroscope:latest` (not old `pyroscope/pyroscope` which lacks gRPC Push API)

---

## Task 2: Orion Changepoint Detection

### Step 2.1: Create Orion Config

```yaml
# File: orion-config/lcs-load-generator.yaml
tests:
  - name: lcs-post-query
    index: lcs-perf-results*
    benchmarkIndex: lcs-perf-results*
    metadata:
      ocpVersion: clusterVersion
      platform: platform
    metrics:
      - name: p99Latency-post-query
        metricName: post_query
        metric_of_interest: p99Latency
        agg: { value: avg, agg_type: avg }
      - name: throughput-post-query
        metricName: post_query
        metric_of_interest: throughput
        agg: { value: avg, agg_type: avg }
      - name: avgLatency-post-query
        metricName: post_query
        metric_of_interest: avgLatency
        agg: { value: avg, agg_type: avg }

  - name: lcs-post-streaming-query
    index: lcs-perf-results*
    benchmarkIndex: lcs-perf-results*
    metrics:
      - name: p99Latency-streaming
        metricName: post_streaming_query
        metric_of_interest: p99Latency
        agg: { value: avg, agg_type: avg }
      - name: ttft-p99-streaming
        metricName: post_streaming_query
        metric_of_interest: ttft_p99
        agg: { value: avg, agg_type: avg }
      - name: throughput-streaming
        metricName: post_streaming_query
        metric_of_interest: throughput
        agg: { value: avg, agg_type: avg }
```

### Step 2.2: Validate Locally

```bash
orion cmd \
  --config orion-config/lcs-load-generator.yaml \
  --lookback 180 \
  --lookback-size 15 \
  --hunter-analyze \
  --output junit \
  --es-url $ES_SERVER
```

---

## Task 3: CI Pipeline (Prow Periodic Jobs)

### Overview

Three PRs needed:
1. **`lightspeed-core/lcs-load-generator`** — `Containerfile`, `prow/`, `orion-config/`
2. **`openshift/release`** — ci-operator config + step registry ref/commands
3. **`cloud-bulldozer/utils`** — ES reindexer for `lcs-perf-results`

### Step 3.1: LCS Overlay Image in CI

**Problem:** `dev-latest` is the production image — no `pyroscope-io` or `memray` installed (dev dependencies, not shipped to customers).

**Solution:** Build a thin overlay image in CI that extends `dev-latest` with profiling tools. Rebuilt automatically from current `dev-latest` on every CI run — always in sync, zero maintenance.

**`prow/Containerfile.lcs-perf`:**
```dockerfile
# Stage 1: install profiling packages (python:3.12-slim has pip, runtime image doesn't)
FROM python:3.12-slim AS installer
RUN pip install pyroscope-io memray --target /packages

# Stage 2: extend LCS runtime image — no pip needed
FROM quay.io/lightspeed-core/lightspeed-stack:dev-latest

USER 0

# cp -rn = no-clobber: adds profiling packages without overwriting LCS pinned deps
RUN --mount=type=bind,from=installer,source=/packages,target=/tmp/new-pkgs \
    cp -rn /tmp/new-pkgs/. /app-root/.venv/lib/python3.12/site-packages/

# Copy profiling changes from our source (must be in sync with dev-latest)
COPY src/observability/profiling.py /app-root/src/observability/profiling.py
COPY src/app/main.py /app-root/src/app/main.py
COPY scripts/entrypoint.sh /app-root/entrypoint.sh
RUN chmod +x /app-root/entrypoint.sh

USER 1001
```

### Step 3.2: ci-operator Config

**File:** `ci-operator/config/lightspeed-core/lcs-load-generator/lightspeed-core-lcs-load-generator-main.yaml`

```yaml
build_root:
  image_stream_tag:
    name: release
    namespace: openshift
    tag: golang-1.21

base_images:
  # Pull LCS dev image from lightspeed-core image stream — always latest
  lcs-base:
    namespace: lightspeed-core
    name: lightspeed-stack
    tag: dev-latest

images:
  # Image 1: Load generator (Locust tool)
  - dockerfile_path: Containerfile
    to: lcs-load-generator-ci

  # Image 2: LCS perf overlay (dev-latest + pyroscope-io + memray)
  # inputs: maps lcs-base image stream tag to replace the FROM line in Containerfile.lcs-perf
  # This avoids hardcoding quay.io URLs — ci-operator resolves the image stream tag
  - dockerfile_path: prow/Containerfile.lcs-perf
    inputs:
      lcs-base:
        as:
        - quay.io/lightspeed-core/lightspeed-stack:dev-latest
    to: lightspeed-stack-perf

promotion:
  to:
  - namespace: lcs-load-generator
    tag: latest

releases:
  latest:
    candidate:
      product: ocp
      stream: nightly
      version: "4.17"

resources:
  '*':
    requests:
      cpu: 100m
      memory: 200Mi

tests:
- as: lcs-load-test-10users
  cron: 0 0 * * 1
  steps:
    allow_best_effort_post_steps: true
    allow_skip_on_success: true
    cluster_profile: aws-perfscale-qe
    env:
      BASE_DOMAIN: qe.devcluster.openshift.com
      COMPUTE_NODE_REPLICAS: "3"
      COMPUTE_NODE_TYPE: m6i.xlarge
      LOCUST_USERS: "10"
      LOCUST_RUN_TIME: "10m"
      LCS_ES_INDEX: lcs-perf-results
      ES_BENCHMARK_INDEX: lcs-perf-results*
      ES_METADATA_INDEX: perf_scale_ci*
      ENABLE_PYROSCOPE: "true"
      ENABLE_MEMRAY: "false"
      LOOKBACK: "180"
      LOOKBACK_SIZE: "15"
      ORION_CONFIG: orion-config/lcs-load-generator.yaml
      OUTPUT_FORMAT: JUNIT
      RUN_ORION: "true"
      ZONES_COUNT: "3"
    test:
    - ref: lcs-load-generator-tests
    - ref: openshift-qe-orion
    workflow: openshift-qe-installer-aws

- as: lcs-load-test-25users
  cron: 0 6 * * 1
  steps:
    allow_best_effort_post_steps: true
    allow_skip_on_success: true
    cluster_profile: aws-perfscale-qe
    env:
      BASE_DOMAIN: qe.devcluster.openshift.com
      COMPUTE_NODE_REPLICAS: "3"
      COMPUTE_NODE_TYPE: m6i.xlarge
      LOCUST_USERS: "25"
      LOCUST_RUN_TIME: "10m"
      LCS_ES_INDEX: lcs-perf-results
      ES_BENCHMARK_INDEX: lcs-perf-results*
      ES_METADATA_INDEX: perf_scale_ci*
      ENABLE_PYROSCOPE: "true"
      ENABLE_MEMRAY: "false"
      LOOKBACK: "180"
      LOOKBACK_SIZE: "15"
      ORION_CONFIG: orion-config/lcs-load-generator.yaml
      OUTPUT_FORMAT: JUNIT
      RUN_ORION: "true"
      ZONES_COUNT: "3"
    test:
    - ref: lcs-load-generator-tests
    - ref: openshift-qe-orion
    workflow: openshift-qe-installer-aws

- as: lcs-load-test-50users
  cron: 0 12 * * 1
  steps:
    allow_best_effort_post_steps: true
    allow_skip_on_success: true
    cluster_profile: aws-perfscale-qe
    env:
      BASE_DOMAIN: qe.devcluster.openshift.com
      COMPUTE_NODE_REPLICAS: "3"
      COMPUTE_NODE_TYPE: m6i.xlarge
      LOCUST_USERS: "50"
      LOCUST_RUN_TIME: "10m"
      LCS_ES_INDEX: lcs-perf-results
      ES_BENCHMARK_INDEX: lcs-perf-results*
      ES_METADATA_INDEX: perf_scale_ci*
      ENABLE_PYROSCOPE: "true"
      ENABLE_MEMRAY: "false"
      LOOKBACK: "180"
      LOOKBACK_SIZE: "15"
      ORION_CONFIG: orion-config/lcs-load-generator.yaml
      OUTPUT_FORMAT: JUNIT
      RUN_ORION: "true"
      ZONES_COUNT: "3"
    test:
    - ref: lcs-load-generator-tests
    - ref: openshift-qe-orion
    workflow: openshift-qe-installer-aws

- as: lcs-load-test-100users
  cron: 0 18 * * 1
  steps:
    allow_best_effort_post_steps: true
    allow_skip_on_success: true
    cluster_profile: aws-perfscale-qe
    env:
      BASE_DOMAIN: qe.devcluster.openshift.com
      COMPUTE_NODE_REPLICAS: "3"
      COMPUTE_NODE_TYPE: m6i.xlarge
      LOCUST_USERS: "100"
      LOCUST_RUN_TIME: "10m"
      LCS_ES_INDEX: lcs-perf-results
      ES_BENCHMARK_INDEX: lcs-perf-results*
      ES_METADATA_INDEX: perf_scale_ci*
      ENABLE_PYROSCOPE: "true"
      ENABLE_MEMRAY: "false"
      LOOKBACK: "180"
      LOOKBACK_SIZE: "15"
      ORION_CONFIG: orion-config/lcs-load-generator.yaml
      OUTPUT_FORMAT: JUNIT
      RUN_ORION: "true"
      ZONES_COUNT: "3"
    test:
    - ref: lcs-load-generator-tests
    - ref: openshift-qe-orion
    workflow: openshift-qe-installer-aws

zz_generated_metadata:
  branch: main
  org: lightspeed-core
  repo: lcs-load-generator
```

### Step 3.3: Step Ref

**File:** `ci-operator/step-registry/lcs/load-generator/tests/lcs-load-generator-tests-ref.yaml`

```yaml
ref:
  as: lcs-load-generator-tests
  from_image:
    namespace: lcs-load-generator
    name: lcs-load-generator-ci
    tag: latest
  cli: latest
  timeout: 4h
  commands: lcs-load-generator-tests-commands.sh
  credentials:
  - namespace: test-credentials
    name: ocp-qe-perfscale-es
    mount_path: /secret
  resources:
    requests:
      cpu: 100m
      memory: 100Mi
  env:
  - name: LOCUST_USERS
    default: "25"
    documentation: Number of concurrent users
  - name: LOCUST_RUN_TIME
    default: "10m"
    documentation: Test duration per endpoint
  - name: LCS_ES_INDEX
    default: "lcs-perf-results"
    documentation: ES index for all results (Locust + Prometheus)
  - name: ENABLE_PYROSCOPE
    default: "true"
    documentation: Deploy Grafana Pyroscope and enable CPU profiling on LCS
  - name: ENABLE_MEMRAY
    default: "false"
    documentation: Enable Memray memory profiling (dedicated run, high overhead)
  - name: METRIC_STEP
    default: "30"
    documentation: Prometheus scrape step in seconds
```

### Step 3.4: Step Commands

**File:** `ci-operator/step-registry/lcs/load-generator/tests/lcs-load-generator-tests-commands.sh`

This script is **fully self-contained** — all LCS configs are embedded inline. Nothing is referenced from the load generator repo except the load generator Job image itself.

```bash
#!/bin/bash
set -euo pipefail

ES_PASSWORD=$(cat /secret/password)
ES_USERNAME=$(cat /secret/username)
ES_HOST="search-ocp-qe-perf-scale-test-elk-hcm7wtsqpxy7xogbu72bor4uve.us-east-1.es.amazonaws.com"
ES_SERVER="https://${ES_USERNAME}:${ES_PASSWORD}@${ES_HOST}"

LCS_NAMESPACE="openshift-lcs"
PYROSCOPE_NAMESPACE="pyroscope"
LOAD_GEN_NAMESPACE="lcs-perf-testing"
# Promoted by ci-operator from Containerfile.lcs-perf (in this step registry dir)
LCS_IMAGE="${IMAGE_FORMAT%:*}:lightspeed-stack-perf"
UUID=$(uuidgen)

mkdir -p "${ARTIFACT_DIR}/profiling-data"

# ── 1. Deploy Pyroscope ──────────────────────────────────────────────────────
if [[ "${ENABLE_PYROSCOPE}" == "true" ]]; then
  oc new-project "${PYROSCOPE_NAMESPACE}" || true
  oc apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: pyroscope
  namespace: pyroscope
  labels:
    app: pyroscope
spec:
  containers:
  - name: pyroscope
    image: grafana/pyroscope:latest
    ports:
    - containerPort: 4040
---
apiVersion: v1
kind: Service
metadata:
  name: pyroscope
  namespace: pyroscope
spec:
  selector:
    app: pyroscope
  ports:
  - port: 4040
    targetPort: 4040
EOF
  oc wait --for=condition=Ready pod/pyroscope -n "${PYROSCOPE_NAMESPACE}" --timeout=120s
fi

# ── 2. Deploy LCS (library mode + mock LLM sidecar) ─────────────────────────
oc new-project "${LCS_NAMESPACE}" || true
oc label namespace "${LCS_NAMESPACE}" openshift.io/cluster-monitoring=true --overwrite

# lightspeed-stack.yaml — embedded inline
oc create configmap lcs-config -n "${LCS_NAMESPACE}" --dry-run=client -o yaml \
  --from-literal=lightspeed-stack.yaml="$(cat <<'YAML'
name: LCS Performance Testing
service:
  host: 0.0.0.0
  port: 8080
  auth_enabled: false
  workers: 1
ogx:
  use_as_library_client: true
  config:
    profile: /app-root/run.yaml
inference:
  default_provider: openai
  default_model: granite-3.1-8b-instruct
  context_windows:
    "openai/granite-3.1-8b-instruct": 131072
    "openai/llama-guard-3-8b": 131072
user_data_collection:
  feedback_enabled: false
  transcripts_enabled: false
authentication:
  module: "noop"
YAML
)" | oc apply -f -

# run.yaml — embedded inline
oc create configmap lcs-run-config -n "${LCS_NAMESPACE}" --dry-run=client -o yaml \
  --from-literal=run.yaml="$(cat <<'YAML'
version: 2
distro_name: starter
apis: [responses, conversations, files, file_processors, inference, tool_runtime, vector_io]
providers:
  inference:
  - provider_id: openai
    provider_type: remote::openai
    config:
      api_key: fake-key-for-testing
      base_url: http://localhost:11434/v1
  - config: {}
    provider_id: sentence-transformers
    provider_type: inline::sentence-transformers
  files:
  - config:
      metadata_store:
        table_name: files_metadata
        backend: sql_default
      storage_dir: /tmp/llama-storage/files
    provider_id: meta-reference-files
    provider_type: inline::localfs
  file_processors:
  - provider_id: pypdf
    provider_type: inline::pypdf
    config:
      default_chunk_size_tokens: 800
      default_chunk_overlap_tokens: 400
  tool_runtime:
  - config: {}
    provider_id: model-context-protocol
    provider_type: remote::model-context-protocol
  - config: {}
    provider_id: file-search
    provider_type: inline::file-search
  vector_io:
  - provider_id: faiss
    provider_type: inline::faiss
    config:
      persistence:
        namespace: vector_io::faiss
        backend: kv_default
  responses:
  - config:
      persistence:
        responses:
          table_name: agents_responses
          backend: sql_default
    provider_id: meta-reference
    provider_type: inline::builtin
server:
  port: 8321
storage:
  backends:
    kv_default:
      type: kv_sqlite
      db_path: /tmp/llama-storage/kv_store.db
    sql_default:
      type: sql_sqlite
      db_path: /tmp/llama-storage/sql_store.db
  stores:
    metadata:
      namespace: registry
      backend: kv_default
    inference:
      table_name: inference_store
      backend: sql_default
      max_write_queue_size: 10000
      num_writers: 4
    conversations:
      table_name: openai_conversations
      backend: sql_default
    prompts:
      table_name: prompts
      backend: sql_default
    connectors:
      table_name: connectors
      backend: sql_default
registered_resources:
  models:
  - model_id: granite-3.1-8b-instruct
    model_type: llm
    provider_id: openai
    provider_model_id: granite-3.1-8b-instruct
  - model_id: llama-guard-3-8b
    model_type: llm
    provider_id: openai
    provider_model_id: llama-guard-3-8b
vector_stores:
  annotation_prompt_params:
    enable_annotations: false
  default_provider_id: faiss
  default_embedding_model:
    provider_id: sentence-transformers
    model_id: nomic-ai/nomic-embed-text-v1.5
YAML
)" | oc apply -f -

# LCS Deployment + Service — embedded inline
PYROSCOPE_ENV=""
[[ "${ENABLE_PYROSCOPE}" == "true" ]] && \
  PYROSCOPE_ENV="- name: PYROSCOPE_SERVER_ADDRESS
          value: http://pyroscope.${PYROSCOPE_NAMESPACE}.svc.cluster.local:4040"

# Memray command override — wraps LCS with memory profiler when enabled
# NOTE: causes ~50-65% latency overhead — use ENABLE_MEMRAY=false for normal perf runs
MEMRAY_CMD=""
[[ "${ENABLE_MEMRAY}" == "true" ]] && MEMRAY_CMD='command:
        - /app-root/.venv/bin/python
        - -m
        - memray
        - run
        - --force
        - -o
        - /mnt/profiling/memray-lcs.bin
        - src/lightspeed_stack.py'

oc apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lcs
  namespace: ${LCS_NAMESPACE}
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
        ports:
        - containerPort: 8080
        env:
        - name: OTEL_SDK_DISABLED
          value: "true"
        ${PYROSCOPE_ENV}
        volumeMounts:
        - name: lcs-config
          mountPath: /app-root/lightspeed-stack.yaml
          subPath: lightspeed-stack.yaml
        - name: run-config
          mountPath: /app-root/run.yaml
          subPath: run.yaml
        - name: llama-storage
          mountPath: /tmp/llama-storage
        - name: profiling-data
          mountPath: /mnt/profiling
        resources:
          requests:
            cpu: "2"
            memory: "2Gi"
          limits:
            cpu: "4"
            memory: "4Gi"
      - name: mock-llm-server
        image: quay.io/rh-ee-bbodapat/lcs-testing:mock-llm-server
        ports:
        - containerPort: 11434
        env:
        - name: MOCK_MODELS
          value: "granite-3.1-8b-instruct,llama-guard-3-8b"
        - name: MOCK_RESPONSE_TEXT
          value: "This is a mock response for performance testing."
        resources:
          requests:
            cpu: "500m"
            memory: "128Mi"
      volumes:
      - name: lcs-config
        configMap:
          name: lcs-config
      - name: run-config
        configMap:
          name: lcs-run-config
      - name: llama-storage
        emptyDir: {}
      - name: profiling-data
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: lcs
  namespace: ${LCS_NAMESPACE}
spec:
  selector:
    app: lcs
  ports:
  - port: 8080
    targetPort: 8080
EOF

oc wait --for=condition=Available deployment/lcs -n "${LCS_NAMESPACE}" --timeout=300s

# ── 3. Run load test ─────────────────────────────────────────────────────────
oc new-project "${LOAD_GEN_NAMESPACE}" || true

# Create kubeconfig secret for kube-burner Prometheus discovery
oc create secret generic kubeconfig-secret \
  --from-file=kubeconfig="${KUBECONFIG}" \
  -n "${LOAD_GEN_NAMESPACE}" --dry-run=client -o yaml | oc apply -f -

oc apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: lcs-locust-monitoring
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-monitoring-view
subjects:
- kind: ServiceAccount
  name: default
  namespace: ${LOAD_GEN_NAMESPACE}
---
apiVersion: batch/v1
kind: Job
metadata:
  name: lcs-load-generator
  namespace: ${LOAD_GEN_NAMESPACE}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: lcs-load-generator
        image: quay.io/lcs-load-generator/lcs-load-generator-ci:latest
        env:
        - name: KUBECONFIG
          value: /etc/kubeconfig/kubeconfig
        - name: LCS_HOST
          value: "http://lcs.${LCS_NAMESPACE}.svc.cluster.local:8080"
        - name: LCS_NAMESPACE
          value: "${LCS_NAMESPACE}"
        - name: LOCUST_USERS
          value: "${LOCUST_USERS}"
        - name: LOCUST_RUN_TIME
          value: "${LOCUST_RUN_TIME}"
        - name: ES_SERVER
          value: "${ES_SERVER}"
        - name: ES_INDEX
          value: "${LCS_ES_INDEX}"
        - name: METRIC_STEP
          value: "${METRIC_STEP}"
        - name: LCS_PROVIDER
          value: "openai"
        - name: LCS_MODEL
          value: "granite-3.1-8b-instruct"
        volumeMounts:
        - name: kubeconfig-volume
          mountPath: /etc/kubeconfig
          readOnly: true
      volumes:
      - name: kubeconfig-volume
        secret:
          secretName: kubeconfig-secret
EOF

TEST_START=$(date +%s)
oc wait --for=condition=complete job/lcs-load-generator \
  -n "${LOAD_GEN_NAMESPACE}" --timeout=7200s
TEST_END=$(date +%s)

oc logs job/lcs-load-generator -n "${LOAD_GEN_NAMESPACE}" \
  > "${ARTIFACT_DIR}/load-generator.log" 2>&1 || true

# ── 4. Collect profiling artifacts ───────────────────────────────────────────
LCS_POD=$(oc get pod -n "${LCS_NAMESPACE}" -l app=lcs \
  -o jsonpath='{.items[0].metadata.name}')

# CPU profiles from Pyroscope (exact load test window)
if [[ "${ENABLE_PYROSCOPE}" == "true" ]]; then
  PYRO_BASE="http://pyroscope.${PYROSCOPE_NAMESPACE}.svc.cluster.local:4040"
  PYRO_QUERY="process_cpu%3Acpu%3Ananoseconds%3Acpu%3Ananoseconds%7Bservice_name%3D%22lightspeed-stack%22%7D"
  BASE="${PYRO_BASE}/pyroscope/render?query=${PYRO_QUERY}&from=${TEST_START}&until=${TEST_END}"

  curl -sf "${BASE}&format=pprof" \
    -o "${ARTIFACT_DIR}/profiling-data/cpu-profile.pb.gz" || true
  echo "  cpu-profile.pb.gz saved (pprof binary — go tool pprof)"

  curl -sf "${BASE}&format=html" \
    -o "${ARTIFACT_DIR}/profiling-data/cpu-flamegraph.html" || true
  echo "  cpu-flamegraph.html saved"

  curl -sf "${BASE}&format=collapsed" \
    -o "${ARTIFACT_DIR}/profiling-data/cpu-collapsed.txt" || true
  echo "  cpu-collapsed.txt saved (raw stacks)"
fi

# Memory profiles from Memray (only when ENABLE_MEMRAY=true, dedicated run)
# ENABLE_MEMRAY=false by default — distorts latency numbers, run separately
# To enable: set ENABLE_MEMRAY=true in step env and accept ~50-65% overhead
if [[ "${ENABLE_MEMRAY}" == "true" ]]; then
  MEMRAY_BIN="/mnt/profiling/memray-lcs.bin"

  if oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs \
      -- test -f "${MEMRAY_BIN}" 2>/dev/null; then

    # Copy raw binary — use cat (no oc cp, minimal UBI image has no tar)
    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs \
      -- cat "${MEMRAY_BIN}" \
      > "${ARTIFACT_DIR}/profiling-data/memray-lcs.bin" || true
    echo "  memray-lcs.bin saved (raw allocation trace)"

    # Generate flamegraphs inside pod, copy out via cat
    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs -- \
      /app-root/.venv/bin/python -m memray flamegraph \
      "${MEMRAY_BIN}" -o /mnt/profiling/memray-flamegraph.html --force 2>/dev/null || true
    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs \
      -- cat /mnt/profiling/memray-flamegraph.html \
      > "${ARTIFACT_DIR}/profiling-data/memray-flamegraph.html" 2>/dev/null || true
    echo "  memray-flamegraph.html saved (all allocations)"

    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs -- \
      /app-root/.venv/bin/python -m memray flamegraph --leaks \
      "${MEMRAY_BIN}" -o /mnt/profiling/memray-leaks.html --force 2>/dev/null || true
    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs \
      -- cat /mnt/profiling/memray-leaks.html \
      > "${ARTIFACT_DIR}/profiling-data/memray-leaks-flamegraph.html" 2>/dev/null || true
    echo "  memray-leaks-flamegraph.html saved (leaks only)"

    oc exec "${LCS_POD}" -n "${LCS_NAMESPACE}" -c lcs -- \
      /app-root/.venv/bin/python -m memray stats "${MEMRAY_BIN}" \
      > "${ARTIFACT_DIR}/profiling-data/memray-stats.txt" 2>&1 || true
    echo "  memray-stats.txt saved (top allocating functions)"
  else
    echo "  WARNING: ${MEMRAY_BIN} not found — was LCS started with memray command override?"
  fi
fi

echo "Profiling artifacts:"
ls -lh "${ARTIFACT_DIR}/profiling-data/" || true

# ── 5. Cleanup ───────────────────────────────────────────────────────────────
oc delete namespace "${LCS_NAMESPACE}" "${LOAD_GEN_NAMESPACE}" \
  "${PYROSCOPE_NAMESPACE}" --wait=false || true
```

### Step 3.5: Deploy LCS in CI — Key Config Notes

**`config/lightspeed-stack.yaml`** (mounted as configmap):
```yaml
name: LCS Performance Testing
service:
  host: 0.0.0.0
  port: 8080
  auth_enabled: false
  workers: 1
ogx:                              # NOT llama_stack (deprecated)
  use_as_library_client: true
  config:
    profile: /app-root/run.yaml   # absolute path — file is mounted via configmap
inference:
  default_provider: openai
  default_model: granite-3.1-8b-instruct
user_data_collection:
  feedback_enabled: false
  transcripts_enabled: false
authentication:
  module: "noop"
```

**`config/run.yaml`** (mounted as configmap) — must match current OGX/ogx API format:
```yaml
version: 2
distro_name: starter
apis: [responses, conversations, files, file_processors, inference, tool_runtime, vector_io]
providers:
  inference:
  - provider_id: openai
    provider_type: remote::openai
    config:
      api_key: fake-key-for-testing
      base_url: http://localhost:11434/v1   # mock LLM sidecar
  ...
```

**Deployment env vars required:**
```yaml
env:
- name: PYROSCOPE_SERVER_ADDRESS
  value: "http://pyroscope.pyroscope.svc.cluster.local:4040"  # or ""
- name: OTEL_SDK_DISABLED
  value: "true"   # prevents 500 errors — no OTel backend in perf cluster
```

### Step 3.6: Full CI Pipeline Flow

```
Prow cron trigger (Mon, staggered by concurrency)
  │
  ├── ci-operator builds images:
  │     ├── lcs-load-generator-ci (Containerfile)
  │     └── lightspeed-stack-perf (prow/Containerfile.lcs-perf)
  │         = dev-latest + pyroscope-io + memray + profiling.py + main.py
  │
  ├── openshift-qe-installer-aws: provision 3x m6i.xlarge cluster
  │
  ├── lcs-load-generator-tests step:
  │     ├── Deploy Grafana Pyroscope (grafana/pyroscope:latest)
  │     ├── Deploy LCS (lightspeed-stack-perf image) + Mock LLM sidecar
  │     │     PYROSCOPE_SERVER_ADDRESS → SDK pushes CPU profiles continuously
  │     ├── Run load test (lcs-load-generator-ci Job)
  │     │     Locust → lcs-perf-results (ES)
  │     │     kube-burner → lcs-perf-results (Prometheus metrics)
  │     └── Collect profiling artifacts → ARTIFACT_DIR/profiling-data/
  │           cpu-profile.pb.gz, cpu-flamegraph.html
  │
  ├── openshift-qe-orion step:
  │     Queries ES, changepoint detection, JUNIT output
  │
  ├── Cluster teardown
  │
  └── Slack → #lcs-perf-scale-ci-results
```

### Step 3.7: Verification Checklist

- [ ] `ci-operator-config` validation passes (`make ci-operator-config`)
- [ ] Image build works: `docker buildx build -f prow/Containerfile.lcs-perf ...`
- [ ] LCS starts with perf image and Pyroscope logs "CPU profiling enabled"
- [ ] Load test indexes to ES (post_query, post_streaming_query, containerCPU, etc.)
- [ ] kube-burner scrape works (no `--metrics-profile` path errors)
- [ ] Pyroscope pprof export works (`cpu-profile.pb.gz` > 0 bytes)
- [ ] Orion runs and generates JUNIT
- [ ] Slack notification fires

---

## Task 4: Profiling — Status

### What's Done

| Item | Status |
|------|--------|
| `src/observability/profiling.py` | ✅ Done, PR open |
| `src/app/main.py` — calls `initialize_pyroscope()` | ✅ Done, PR open |
| `pyproject.toml` — dev deps `pyroscope-io`, `memray` | ✅ Done, PR open |
| `prow/Containerfile.lcs-perf` — multi-stage overlay | ✅ Done |
| `collect-profiles.sh` — local collection script | ✅ Done |
| Pyroscope overhead test (3 runs) | ✅ Done — ~0% overhead |
| Memray overhead test | ✅ Done — ~50-65% overhead |

### Profiling Overhead Results (25 users, 5 min)

| Run | Query p50 | Query p99 | TTFT p50 | Throughput |
|---|---|---|---|---|
| Baseline | 1800ms | 5100ms | 94ms | 12.1 req/s |
| + Pyroscope | 1800ms | 5100ms | 94ms | 12.18 req/s |
| + Memray | 2800ms | 7200ms | 155ms | 7.93 req/s |

**Pyroscope: zero overhead → safe for every CI run.**
**Memray: 50-65% overhead → dedicated sessions only, `ENABLE_MEMRAY: "false"` in CI.**

### Memray in CI (on-demand)

Memray is disabled by default (`ENABLE_MEMRAY: "false"`). To run a dedicated memory profiling session:

```bash
# Override LCS command — no code changes needed
oc patch deployment/lcs -n openshift-lcs --type=json -p='[{
  "op": "add",
  "path": "/spec/template/spec/containers/0/command",
  "value": ["/app-root/.venv/bin/python", "-m", "memray", "run",
             "--force", "-o", "/mnt/profiling/memray-lcs.bin",
             "src/lightspeed_stack.py"]
}]'
```

Run load test, then collect:
```bash
LCS_NAMESPACE=openshift-lcs LOCUST_RUN_TIME=5m ./collect-profiles.sh
```

### What's Pending on LCS PR

- [x] `src/observability/profiling.py` (moved from `src/profiling.py`)
- [x] `src/app/main.py` — import path updated to `observability.profiling`
- [ ] PR title needs JIRA prefix (LCORE- or RSPEED-)
- [ ] All CI checks green (Black/Pylint — need to push latest fixes)

---

## Task 5: ES Reindexer (Pending)

Set up `cloud-bulldozer/utils` es-reindexer to sync `lcs-perf-results` from perf-scale ES to internal long-term ES. Needed for Orion historical data retention.

Steps: PR to `cloud-bulldozer/utils` + systemd timer on the reindexer host. Follow existing OLS reindexer as template.

---

## Implementation Order (Remaining)

| Priority | Task | Effort |
|---|---|---|
| 1 | Push LCS profiling PR with JIRA prefix + green CI | Low |
| 2 | Create Orion config + validate locally | Low |
| 3 | Submit `openshift/release` PR (step ref + ci-operator config) | Medium |
| 4 | Submit `lightspeed-core/lcs-load-generator` PR (prow/ files) | Low |
| 5 | ES reindexer setup | Medium |
| 6 | Slack channel for CI notifications | Low |
