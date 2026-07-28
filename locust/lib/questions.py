import logging
import os

import yaml

from lib.config import QUESTIONS_FILE

logger = logging.getLogger("lcs.questions")

FALLBACK_QUESTIONS = [
    "What is a pod in OpenShift?",
    "How do I create a deployment?",
    "What is a service in Kubernetes?",
    "How do I scale my application?",
    "What are ConfigMaps used for?",
    "How do I set up persistent storage?",
    "What is an Operator in OpenShift?",
    "How do I configure resource limits?",
    "What is a Route in OpenShift?",
    "How do I debug a failing pod?",
]


def load_questions() -> list[str]:
    if os.path.exists(QUESTIONS_FILE):
        with open(QUESTIONS_FILE) as f:
            data = yaml.safe_load(f)
            questions = data.get("questions", []) if isinstance(data, dict) else data
        logger.debug("Loaded %d questions from %s", len(questions), QUESTIONS_FILE)
        return questions
    logger.debug("Questions file not found (%s), using %d fallback questions",
                 QUESTIONS_FILE, len(FALLBACK_QUESTIONS))
    return FALLBACK_QUESTIONS


QUESTIONS = load_questions()
