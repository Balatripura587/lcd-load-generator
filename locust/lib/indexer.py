"""Thin wrapper around py-commons indexer for Locust subprocess use."""

import logging
import os

from commons.indexers import IndexerConfig, new_indexer
from lib.config import ES_INDEX, ES_SERVER, RESULTS_DIR, TEST_UUID

logger = logging.getLogger("lcs.indexer")

_config = IndexerConfig(
    type="opensearch" if ES_SERVER else "local",
    servers=[ES_SERVER] if ES_SERVER else [],
    index=ES_INDEX,
    insecure_skip_verify=True,
    metrics_directory=os.path.join(RESULTS_DIR, f"collected-metrics-{TEST_UUID}"),
)

_indexer = None


def _get_indexer():
    """Return a lazily-initialized singleton indexer instance."""
    global _indexer
    if _indexer is None:
        _indexer = new_indexer(_config)
    return _indexer


def index_results(results: dict):
    """Index a single result document to ES or local file."""
    metric_name = results.get("metricName", "results")
    indexer = _get_indexer()
    msg = indexer.index([results], metric_name=metric_name)
    logger.info(msg)
