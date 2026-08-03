"""Document indexer with Elasticsearch/OpenSearch and local file backends.

Mirrors go-commons indexers package. Provides a config-driven interface so
callers don't hard-code connection details — pass an IndexerConfig instead.
"""

import json
import logging
import os

logger = logging.getLogger("py_commons.indexers")


class IndexerConfig:
    """Indexer configuration matching go-commons indexers.IndexerConfig."""

    def __init__(self, es_server="", es_index="", results_dir="/tmp",
                 verify_certs=False, test_uuid=""):
        self.es_server = es_server
        self.es_index = es_index
        self.results_dir = results_dir
        self.verify_certs = verify_certs
        self.test_uuid = test_uuid


def index_results(document: dict, config: IndexerConfig):
    """Index a single document to Elasticsearch or a local JSON file."""
    if config.es_server:
        _index_to_elasticsearch(document, config)
    else:
        _index_to_local(document, config)


def index_bulk(documents: list[dict], config: IndexerConfig):
    """Index multiple documents."""
    for doc in documents:
        index_results(doc, config)


def _index_to_elasticsearch(document: dict, config: IndexerConfig):
    try:
        from elasticsearch import Elasticsearch

        logger.debug("Connecting to Elasticsearch: %s", config.es_server)
        es = Elasticsearch(config.es_server, verify_certs=config.verify_certs)
        doc_id = f"{document.get('uuid', 'unknown')}-{document.get('metricName', 'results')}"
        es.index(index=config.es_index, body=document, id=doc_id)
        logger.info("Indexed to %s/%s (doc_id=%s)", config.es_server, config.es_index, doc_id)
    except ImportError:
        logger.warning("elasticsearch package not installed, falling back to local")
        _index_to_local(document, config)
    except Exception as e:
        logger.warning("Failed to index to Elasticsearch: %s", e)
        _index_to_local(document, config)


def _index_to_local(document: dict, config: IndexerConfig):
    test_uuid = config.test_uuid or document.get("uuid", "unknown")
    metrics_dir = os.path.join(config.results_dir, f"collected-metrics-{test_uuid}")
    os.makedirs(metrics_dir, exist_ok=True)
    metric_name = document.get("metricName", "results")
    out_path = os.path.join(metrics_dir, f"{metric_name}.json")
    with open(out_path, "w") as f:
        json.dump(document, f, indent=2)
    logger.info("Local index: %s", out_path)
