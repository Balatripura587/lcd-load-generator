"""Thin wrapper around py_commons.indexers for the Locust subprocess.

Reads ES config from environment at import time (via lib.config) and exposes
the same ``index_results(document)`` interface that metrics.py calls.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from py_commons.indexers import IndexerConfig, index_results as _index

from lib.config import ES_INDEX, ES_SERVER, RESULTS_DIR, TEST_UUID

logger = logging.getLogger("lcs.indexer")

_config = IndexerConfig(
    es_server=ES_SERVER,
    es_index=ES_INDEX,
    results_dir=RESULTS_DIR,
    test_uuid=TEST_UUID,
)


def index_results(results: dict):
    """Index a single result document — delegates to py_commons."""
    _index(results, _config)
