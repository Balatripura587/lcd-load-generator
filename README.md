# lcs-load-generator

Load generator tool for [LightSpeed Core Service (LCS)](https://github.com/lightspeed-core/lightspeed-stack) using [Locust](https://locust.io/).

Simulates multiple user sessions to perform duration-based load tests with configurable parallelism. Runs LCS endpoints sequentially in a single invocation, collecting latency, throughput, TTFT, and HTTP status code metrics per endpoint.

Supported endpoints:

| Endpoint type | Path | Notes |
|---|---|---|
| `query` | `POST /v1/query` | Non-streaming |
| `streaming` | `POST /v1/streaming_query` | SSE, TTFT tracked |
| `responses` | `POST /v1/responses` `stream=false` | OpenAI-compatible Responses API |
| `streaming_responses` | `POST /v1/responses` `stream=true` | OpenAI-compatible SSE, TTFT tracked |

Works with both **library mode** and **server mode** LCS deployments. The load generator only needs the LCS HTTP URL (`LCS_HOST`) — set it to whichever URL LCS is listening on

## Prerequisites

### Running on OpenShift cluster

LCS deployed on an OpenShift cluster with a configured LLM provider (e.g. OpenAI, vLLM, WatsonX via Llama Stack). No separate inference layer is needed — LCS handles inference routing internally. Refer to the [lightspeed-stack](https://github.com/lightspeed-core/lightspeed-stack) setup instructions.

### Running on local machine

A running instance of LCS with a configured LLM backend. Refer to the [lightspeed-stack](https://github.com/lightspeed-core/lightspeed-stack) setup instructions for local deployment.

## Setting Up Prometheus Monitoring for LCS

LCS does not have an operator. LCS requires manual Prometheus setup for the load generator to scrape application metrics (`ls_llm_calls_total`, `ls_llm_token_sent_total`, etc.).

Without this setup, load tests still run and index Locust results, but Prometheus metrics (container CPU/memory, LCS application metrics) will be empty.

### Option A: `openshift-*` Namespace — Platform Prometheus (Recommended)

If LCS is deployed in an `openshift-*` namespace (e.g. `openshift-lcs`), platform Prometheus (`prometheus-k8s`) scrapes it directly.

```bash
# 1. Label the namespace for platform Prometheus
oc label namespace $LCS_NAMESPACE openshift.io/cluster-monitoring=true

# 2. Apply RBAC + ServiceMonitor
envsubst < config/monitoring/platform-prometheus.yaml | oc apply -f -

# 3. Verify after ~60s (first scrape cycle)
TOKEN=$(oc create token prometheus-k8s -n openshift-monitoring --duration=600s)
PROM=$(oc get route prometheus-k8s -n openshift-monitoring -o jsonpath='{.spec.host}')
curl -sk -H "Authorization: Bearer $TOKEN" \
  "https://$PROM/api/v1/query?query=ls_llm_calls_total" | python3 -m json.tool
```

Requirements:
1. Namespace label: `openshift.io/cluster-monitoring=true`
2. RBAC: Role + RoleBinding granting `prometheus-k8s` SA access to services/endpoints/pods (included in `platform-prometheus.yaml`)
3. LCS Service must have `app: lcs` in its **metadata labels** (ServiceMonitor matches on this)

No extra env vars needed on the load generator — `prometheus-k8s` is the default backend.

### Option B: User Namespace — Thanos Querier

If LCS is deployed in a non-`openshift-*` namespace (e.g. `lcs-perf-testing`), enable user workload monitoring and query via Thanos Querier.

```bash
# 1. Enable user workload monitoring + create ServiceMonitor
envsubst < config/monitoring/user-workload-thanos.yaml | oc apply -f -

# 2. Wait for user-workload Prometheus to start
oc rollout status statefulset/prometheus-user-workload \
  -n openshift-user-workload-monitoring --timeout=120s
```

Set `PROMETHEUS_BACKEND=thanos` on the load generator Job/container.

### Monitoring Options Comparison

| | Option A (`openshift-*`) | Option B (user namespace) |
|---|---|---|
| Namespace | Must be `openshift-*` | Any |
| Prometheus | Platform (`prometheus-k8s`) | User-workload via Thanos Querier |
| Cluster setup | Namespace label + RBAC | Enable user workload monitoring ConfigMap |
| `PROMETHEUS_BACKEND` env | Not needed (default) | `thanos` |
| Config file | `config/monitoring/platform-prometheus.yaml` | `config/monitoring/user-workload-thanos.yaml` |

## Installation

```bash
pip install -r requirements.txt

# Install py-commons with indexers and ocp_metadata extras
pip install "rh-py-commons[ocp_metadata,indexers] @ git+https://github.com/cloud-bulldozer/py-commons.git"
```

Ensure `locust` is available in your `$PATH` after installation.

To build the container image:

```
make build
```

## Usage

```
lcs-load-generator [flags] <command> [command flags]

FLAGS:
   -D          print debugging logs (default: false)
   -W          quieter log output (default: false)

COMMANDS:
   run         Run load tests on all LCS endpoints

RUN FLAGS:
   --host value             LCS endpoint URL (default: http://localhost:8080) [$LCS_HOST]
   --token value            LCS auth token [$LCS_TOKEN]
   --provider value         LLM provider name (default: openai) [$LCS_PROVIDER]
   --model value            LLM model name (default: granite-3.1-8b-instruct) [$LCS_MODEL]
   --users value            Concurrent users (default: 10) [$LOCUST_USERS]
   --duration value         Test duration e.g. 5m (default: 1m) [$LOCUST_RUN_TIME]
   --uuid value             Test UUID, auto-generated if not set [$TEST_UUID]
   --request-timeout value  Per-request timeout in seconds (default: 120) [$REQUEST_TIMEOUT]
   --es-server value        Elasticsearch URL [$ES_SERVER]
   --es-index value         ES index for results (default: lcs-perf-results) [$ES_INDEX]
   --processes value        Locust worker processes (default: 1) [$LOCUST_PROCESSES]
   --results-dir value      Results directory (default: /tmp) [$RESULTS_DIR]
   --questions-file value   Path to questions YAML [$QUESTIONS_FILE]
```

### Mode 1: CLI (local machine)

Run directly from source. All settings can be passed as CLI flags or environment variables (flag takes precedence).

```bash
./lcs-load-generator run \
  --host http://localhost:8080 \
  --token "your-auth-token" \
  --provider openai \
  --model granite-3.1-8b-instruct \
  --users 10 \
  --duration 1m
```

With Elasticsearch indexing:

```bash
./lcs-load-generator run \
  --host http://localhost:8080 \
  --token "your-auth-token" \
  --users 25 \
  --duration 5m \
  --es-server http://es:9200
```

With multiple worker processes (for high concurrency):

```bash
./lcs-load-generator run \
  --host http://localhost:8080 \
  --token "your-auth-token" \
  --users 100 \
  --duration 10m \
  --processes 4
```

### Mode 2: Container (local or CI)

All configuration via environment variables. The container ENTRYPOINT runs `lcs-load-generator run` automatically — no command override needed.

```bash
make build

podman run --rm \
  -e LCS_HOST=http://lcs:8080 \
  -e LCS_TOKEN="your-auth-token" \
  -e LCS_PROVIDER=openai \
  -e LCS_MODEL=granite-3.1-8b-instruct \
  -e LOCUST_USERS=10 \
  -e LOCUST_RUN_TIME=1m \
  quay.io/bbodapat/lcs-load-generator:latest
```

With Elasticsearch:

```bash
podman run --rm \
  -e LCS_HOST=http://lcs:8080 \
  -e LCS_TOKEN="your-auth-token" \
  -e LOCUST_USERS=25 \
  -e LOCUST_RUN_TIME=5m \
  -e ES_SERVER=http://es:9200 \
  -e ES_INDEX=lcs-perf-results \
  quay.io/bbodapat/lcs-load-generator:latest
```

### Mode 3: OpenShift Job

Create the namespace and kubeconfig secret:

```bash
oc new-project lcs-perf-testing

oc create secret generic kubeconfig-secret \
  --from-file=kubeconfig=$KUBECONFIG \
  -n lcs-perf-testing
```

Set environment variables and deploy using `envsubst`:

```bash
export LCS_NAMESPACE=lcs-perf-testing
export LCS_LOADGEN_IMAGE=quay.io/bbodapat/lcs-load-generator:latest
export LCS_HOST=https://lcs.apps.cluster.example.com
export LCS_TOKEN=your-auth-token
export LCS_PROVIDER=openai
export LCS_MODEL=granite-3.1-8b-instruct
export LOCUST_USERS=10
export LOCUST_RUN_TIME=1m
export LOCUST_PROCESSES=1
export REQUEST_TIMEOUT=120
export ES_SERVER=https://es:9200
export ES_INDEX=lcs-perf-results
export METRIC_STEP=30

envsubst < config/lcs-load-generator.yaml | oc apply -f -
```

Once applied, it creates a Job in the specified namespace and starts running the tests. Tail the logs to see benchmark results:

```bash
oc logs -f job/lcs-load-generator -n lcs-perf-testing
```

The per-endpoint result JSON documents are printed to stdout, so `oc logs` is the primary way to view results.

To re-run, delete the previous Job first:

```bash
oc delete job lcs-load-generator -n lcs-perf-testing --ignore-not-found
envsubst < config/lcs-load-generator.yaml | oc apply -f -
```

## Envs

* `LCS_NAMESPACE` - Namespace where LCS is deployed. Used by Prometheus metric profiles to filter container metrics. Defaults to `openshift-lcs`.
* `LCS_HOST` - LCS endpoint URL to perform load testing.
* `LCS_TOKEN` - LCS auth token string.
* `LCS_PROVIDER` - LLM provider name (e.g. `openai`).
* `LCS_MODEL` - LLM model name (e.g. `granite-3.1-8b-instruct`).
* `LOCUST_USERS` - Number of concurrent simulated users to trigger load on LCS.
* `LOCUST_RUN_TIME` - Load testing duration on each API endpoint (e.g. `1m`, `5m`).
* `LOCUST_PROCESSES` - Number of Locust worker processes for high concurrency.
* `REQUEST_TIMEOUT` - Per-request timeout in seconds.
* `TEST_UUID`(Optional) - Unique test run identifier. Auto-generated if not specified.
* `ES_SERVER`(Optional) - Elasticsearch host URL. If not specified, results are indexed locally.
* `ES_INDEX`(Optional) - Elasticsearch index name. If not specified, defaults to `lcs-perf-results`.
* `RESULTS_DIR`(Optional) - Directory for result files. Defaults to `/tmp`.
* `QUESTIONS_FILE`(Optional) - Path to questions YAML file. Defaults to bundled questions.
* `PROMETHEUS_BACKEND`(Optional) - Prometheus backend to query. `prometheus` (default, uses `prometheus-k8s` route) or `thanos` (uses `thanos-querier` route, required for user namespaces).
* `METRIC_STEP`(Optional) - Prometheus scrape step interval in seconds. Defaults to `30`.
