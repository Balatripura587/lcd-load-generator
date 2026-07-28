import os
import uuid

ENDPOINT_TYPE = os.environ.get("ENDPOINT_TYPE", "query").lower()
LCS_TOKEN = os.environ.get("LCS_TOKEN", "")
LCS_PROVIDER = os.environ.get("LCS_PROVIDER", "openai")
LCS_MODEL = os.environ.get("LCS_MODEL", "granite-3.1-8b-instruct")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))
RESULTS_DIR = os.environ.get("RESULTS_DIR", "/tmp")
TEST_UUID = os.environ.get("TEST_UUID", str(uuid.uuid4()))
ES_SERVER = os.environ.get("ES_SERVER", "")
ES_INDEX = os.environ.get("ES_INDEX", "lcs-perf-results")
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
QUESTIONS_FILE = os.environ.get(
    "QUESTIONS_FILE", os.path.join(_ASSETS_DIR, "questions.yaml")
)
