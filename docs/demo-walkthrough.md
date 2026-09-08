# LCS Load Generator — Demo Walkthrough

## Overview

`lcs-load-generator` is a Locust-based load testing tool for LightSpeed Core Service. It simulates concurrent user sessions against LCS endpoints, collects latency/throughput metrics, scrapes Prometheus container metrics, and indexes everything to Elasticsearch for analysis.

---

## 1. Tool Options

```
./lcs-load-generator run --help

RUN FLAGS:
  --host value         LCS endpoint URL              [$LCS_HOST]
  --token value        LCS auth token                [$LCS_TOKEN]
  --provider value     LLM provider (default: openai)[$LCS_PROVIDER]
  --model value        LLM model name                [$LCS_MODEL]
  --users value        Concurrent users (default: 10) [$LOCUST_USERS]
  --duration value     Test duration e.g. 5m         [$LOCUST_RUN_TIME]
  --es-server value    Elasticsearch URL             [$ES_SERVER]
  --es-index value     ES index (default: lcs-perf-results) [$ES_INDEX]
  --processes value    Locust worker processes       [$LOCUST_PROCESSES]
  --results-dir value  Results directory             [$RESULTS_DIR]
```

The tool also has an `index` subcommand for standalone Prometheus metric scraping after a test:
```
./lcs-load-generator index --start <epoch> --end <epoch> --es-server <url>
```

---

## 2. What It Tests

Runs two endpoints sequentially, each for the configured duration:

### `/v1/query` (non-streaming)
- Sends POST requests with random questions from `questions.yaml`
- Each virtual user maintains a **conversation** — first request creates it, subsequent requests reuse the conversation ID (simulates real multi-turn usage)
- Measures: end-to-end latency per request

### `/v1/streaming_query` (SSE streaming)
- Same question pattern, but uses Server-Sent Events
- Measures three separate metrics:
  - **Full request latency** — time from send to stream close
  - **TTFT** (Time To First Token) — time until first SSE data event arrives
  - **Full stream duration** — time from first to last token

---

## 3. Output — Metrics Collected

### Per-endpoint result document (indexed to ES)
```json
{
  "metricName": "post_query",
  "uuid": "abc123...",
  "users": 25,
  "duration": "300s",
  "throughput": 12.18,
  "p50Latency": 1800,
  "p95Latency": 3700,
  "p99Latency": 5100,
  "maxLatency": 8939,
  "avgLatency": 2042,
  "statusCodes": { "200": 3644 },
  "requests": 3644
}
```

### Streaming-specific fields
```json
{
  "metricName": "post_streaming_query",
  "ttft_p50": 94,
  "ttft_p95": 470,
  "ttft_p99": 1231,
  "ttft_avg": 155,
  "streamTime_p50": 1297,
  "streamTime_p95": 2774,
  "streamTime_p99": 3739
}
```

### Job summary (cluster metadata)
```json
{
  "metricName": "jobSummary",
  "clusterVersion": "4.22.2",
  "platform": "AWS",
  "workerNodesCount": 3,
  "workerNodesType": "m6i.xlarge",
  "region": "us-east-1"
}
```

### Prometheus metrics (via kube-burner)
Scraped from cluster Prometheus after the load test. Indexed with metricNames:
- `containerCPU` — CPU time-series
- `containerMemory` — Memory time-series
- `avg-cpu-lcs`, `max-cpu-lcs` — Aggregate CPU
- `avg-memory-lcs`, `max-memory-lcs` — Aggregate memory
- `max-cpu-throttled-lcs` — CPU throttling
- `pod-restarts` — Container restarts
- LCS application metrics: `avg-llm-calls-total`, `avg-llm-token-sent`, etc.

---

## 4. ES Index Structure

All data goes to `lcs-perf-results` index. Filter by `metricName` to separate:

| `metricName` | What it contains |
|---|---|
| `post_query` | `/v1/query` latency + throughput |
| `post_streaming_query` | `/v1/streaming_query` latency + TTFT |
| `jobSummary` | Cluster metadata + test config |
| `containerCPU` | CPU time-series (kube-burner) |
| `containerMemory` | Memory time-series (kube-burner) |
| `avg-cpu-lcs` | Aggregate CPU (kube-burner) |

---

## 5. Deploying LCS for Load Testing (Library Mode)

We deploy LCS with a mock LLM sidecar — eliminates real LLM latency so all measured latency is LCS + Llama Stack overhead only.

```bash
# Create namespace
oc new-project openshift-lcs
oc label namespace openshift-lcs openshift.io/cluster-monitoring=true

# Create configmaps from our config files
oc create configmap lcs-config \
  --from-file=lightspeed-stack.yaml=deploy/library-mode/lightspeed-stack.yaml \
  -n openshift-lcs

oc create configmap lcs-run-config \
  --from-file=run.yaml=deploy/library-mode/run.yaml \
  -n openshift-lcs

# Deploy LCS + mock LLM sidecar
oc apply -f deploy/library-mode/deployment.yaml
oc wait --for=condition=Available deployment/lcs -n openshift-lcs --timeout=300s
```

**Architecture:**
```
Load Generator Pod → POST /v1/query → LCS (port 8080)
                                          ↓
                                    Mock LLM (localhost:11434)
                                    Returns fixed response instantly
```

**lightspeed-stack.yaml (key config):**
```yaml
name: LCS Performance Testing
service:
  host: 0.0.0.0
  port: 8080
  auth_enabled: false
llama_stack:
  use_as_library_client: true
  config:
    profile: /app-root/run.yaml
inference:
  default_provider: openai
  default_model: granite-3.1-8b-instruct
```

---

## 6. Running the Load Test (OpenShift Job)

```bash
# Set env vars
export LCS_LOADGEN_IMAGE=quay.io/rh-ee-bbodapat/lcs-load-generator:latest
export LOCUST_USERS=25
export LOCUST_RUN_TIME=5m
export ES_SERVER="https://ocp-qe:<password>@search-ocp-qe-perf-scale-test-elk-hcm7wtsqpxy7xogbu72bor4uve.us-east-1.es.amazonaws.com:443"

# Apply job
envsubst < config/lcs-load-generator.yaml | oc apply -f -

# Stream logs
oc logs -f job/lcs-load-generator -n lcs-perf-testing
```

---

## 7. Profiling Integration (Optional)

Two optional profiling tools integrated into LCS — both disabled by default, zero overhead when not set.

### Pyroscope — CPU Flamegraphs (always-on, ~3-5% overhead)

```bash
# Deploy Grafana Pyroscope server
oc new-project pyroscope
oc apply -f <pyroscope-server.yaml>   # grafana/pyroscope:latest

# Enable on LCS — just set env var, no restart needed for config
oc set env deployment/lcs \
  PYROSCOPE_SERVER_ADDRESS=http://pyroscope.pyroscope.svc:4040 \
  --containers=lcs -n openshift-lcs
```

**What you get:** Continuous CPU flamegraphs correlated with load test time window. Export as pprof binary or HTML flamegraph.

**How it works:** 6 lines added to `src/observability/profiling.py` — reads env var at startup, starts background thread that samples call stacks 100x/sec, pushes to Pyroscope server every 10s.

### Memray — Memory Flamegraphs (dedicated run, ~50-65% overhead)

```bash
# Override LCS container command — no code changes
oc patch deployment/lcs -n openshift-lcs --type=json -p='[{
  "op": "add",
  "path": "/spec/template/spec/containers/0/command",
  "value": ["/app-root/.venv/bin/python", "-m", "memray", "run",
             "--force", "-o", "/mnt/profiling/memray-lcs.bin",
             "src/lightspeed_stack.py"]
}]'
```

**What you get:** Full heap allocation trace for entire run. Generate flamegraph with `memray flamegraph`. Run in dedicated sessions — not alongside performance measurement.

### Collecting profiles after load test

```bash
cd /path/to/lcs-load-generator

LCS_NAMESPACE=openshift-lcs \
PYROSCOPE_FROM=${TEST_START} \
PYROSCOPE_UNTIL=${TEST_END} \
  ./collect-profiles.sh
```

**Outputs to `./profiling-results/<timestamp>/`:**
```
cpu-profile.pb.gz        ← pprof binary (CPU)
cpu-flamegraph.html      ← CPU flamegraph
memray-lcs.bin           ← raw memory trace
memray-flamegraph.html   ← memory flamegraph
memray-leaks.html        ← leak-only flamegraph
memray-stats.txt         ← top allocators
```

---

## 8. Profiling Overhead Comparison (25 users, 5 min test)

| Run | Query p50 | Query p99 | Streaming TTFT p50 | Throughput |
|---|---|---|---|---|
| Baseline (no profiling) | 1800ms | 5100ms | 94ms | 12.1 req/s |
| + Pyroscope | 1800ms | 5100ms | 94ms | 12.18 req/s |
| + Memray | 2800ms | 7200ms | 155ms | 7.93 req/s |

**Pyroscope: zero measurable overhead. Safe for every run.**
**Memray: ~50-65% overhead. Dedicated sessions only.**

---

## 9. Grafana Dashboards (dittybopper)

Two dashboards available in `config/grafana/`:

**`lcs-perf-dashboard.json`** — Prometheus (cluster metrics during test)
- Container CPU % and memory RSS
- CPU throttle ratio
- Pod restarts
- Network I/O

**`lcs-locust-results-dashboard.json`** — Elasticsearch (Locust results)
- Query + streaming latency percentiles
- TTFT p50/p95/p99
- Throughput
- Concurrent users

Import:
```bash
cd /path/to/performance-dashboards/dittybopper
./deploy.sh -i /path/to/lcs-load-generator/config/grafana/lcs-perf-dashboard.json
./deploy.sh -i /path/to/lcs-load-generator/config/grafana/lcs-locust-results-dashboard.json
```
