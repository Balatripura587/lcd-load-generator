import json
import logging
import os

from lib.config import ES_INDEX, ES_SERVER, RESULTS_DIR, TEST_UUID

logger = logging.getLogger(__name__)


def index_results(results: dict):
    if ES_SERVER:
        _index_to_elasticsearch(results)
    else:
        _index_to_local(results)


def _index_to_elasticsearch(results: dict):
    try:
        from elasticsearch import Elasticsearch

        es = Elasticsearch(ES_SERVER, verify_certs=False)
        doc_id = f"{results['uuid']}-{results.get('metricName', 'results')}"
        es.index(index=ES_INDEX, body=results, id=doc_id)
        print(f"[LCS] Indexed results to {ES_SERVER}/{ES_INDEX}")
    except ImportError:
        logger.warning("elasticsearch package not installed, falling back to local")
        _index_to_local(results)
    except Exception as e:
        logger.warning("Failed to index to Elasticsearch: %s", e)
        _index_to_local(results)


def _index_to_local(results: dict):
    metrics_dir = os.path.join(RESULTS_DIR, f"collected-metrics-{TEST_UUID}")
    os.makedirs(metrics_dir, exist_ok=True)
    metric_name = results.get("metricName", "results")
    out_path = os.path.join(metrics_dir, f"{metric_name}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"[LCS] Local index: {out_path}")
