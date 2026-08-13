ENGINE ?= podman
REGISTRY ?= quay.io
ORG ?= bbodapat
IMAGE_NAME ?= lcs-load-generator
IMAGE_TAG ?= latest
IMAGE = $(REGISTRY)/$(ORG)/$(IMAGE_NAME):$(IMAGE_TAG)

.PHONY: build push lint test clean

build:
	$(ENGINE) build -f Containerfile -t $(IMAGE) .

push: build
	$(ENGINE) push $(IMAGE)

lint:
	python3 -m flake8 locust/ --max-line-length=120 --exclude=__pycache__

test:
	cd locust && python3 -c "from lib.config import *; from lib.questions import QUESTIONS; print(f'{len(QUESTIONS)} questions loaded')"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true
