# lcs-load-generator

Load generator tool for [LightSpeed Core Service (LCS)](https://github.com/lightspeed-core/lightspeed-stack) using [Locust](https://locust.io/).

Simulates multiple user sessions to perform duration-based load tests with configurable parallelism. Runs LCS endpoints (query, streaming) sequentially in a single invocation, collecting latency, throughput, and HTTP status code metrics per endpoint.

## Prerequisites

### Running on OpenShift cluster

LCS deployed on an OpenShift cluster. Refer to the [lightspeed-stack](https://github.com/lightspeed-core/lightspeed-stack) setup instructions.

### Running on local machine

A running instance of LCS (to test against). Refer to the [lightspeed-stack](https://github.com/lightspeed-core/lightspeed-stack) setup instructions for local deployment.

## Installation

```
pip install locust elasticsearch pyyaml
```

Ensure `locust` is available in your `$PATH` after installation.

To build the container image:

```
make build
```

## Usage

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

With multiple worker processes (for high concurrency / GIL mitigation):

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
  quay.io/rh-ee-bbodapat/lcs-load-generator:latest
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
  quay.io/rh-ee-bbodapat/lcs-load-generator:latest
```

### Mode 3: OpenShift Job

Create the namespace and kubeconfig secret:

```bash
oc new-project lcs-perf-testing

oc create secret generic kubeconfig-secret \
  --from-file=kubeconfig=$KUBECONFIG \
  -n lcs-perf-testing
```

Edit environment variables in `config/lcs-load-generator.yaml` with your corresponding values and apply:

```bash
oc apply -f config/lcs-load-generator.yaml
```

Once applied, it creates a Job in the specified namespace and starts running the tests. Tail the logs to see benchmark results:

```bash
oc logs -f job/lcs-load-generator -n lcs-perf-testing
```

The per-endpoint result JSON documents are printed to stdout, so `oc logs` is the primary way to view results.

To re-run, delete the previous Job first:

```bash
oc delete job lcs-load-generator -n lcs-perf-testing --ignore-not-found
oc apply -f config/lcs-load-generator.yaml
```

## Envs

* `LCS_HOST` - LCS endpoint URL to perform load testing.
* `LCS_TOKEN` - LCS auth token string.
* `LCS_PROVIDER` - LLM provider name (e.g. `openai`).
* `LCS_MODEL` - LLM model name (e.g. `granite-3.1-8b-instruct`).
* `LOCUST_USERS` - Number of concurrent simulated users to trigger load on LCS.
* `LOCUST_RUN_TIME` - Load testing duration on each API endpoint (e.g. `1m`, `5m`).
* `LOCUST_PROCESSES` - Number of Locust worker processes for GIL mitigation.
* `REQUEST_TIMEOUT` - Per-request timeout in seconds.
* `TEST_UUID`(Optional) - Unique test run identifier. Auto-generated if not specified.
* `ES_SERVER`(Optional) - Elasticsearch host URL. If not specified, results are indexed locally.
* `ES_INDEX`(Optional) - Elasticsearch index name. If not specified, defaults to `lcs-perf-results`.
* `RESULTS_DIR`(Optional) - Directory for result files. Defaults to `/tmp`.
* `QUESTIONS_FILE`(Optional) - Path to questions YAML file. Defaults to bundled questions.
