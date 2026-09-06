.PHONY: setup test test-unit test-integration test-e2e test-adversarial test-performance run demo fmt collector airgap-bundle

VENV := .venv/bin
PY := $(VENV)/python

setup:
	python3 -m venv .venv
	$(VENV)/pip install -q -e ".[ml,dev]"

test:
	$(VENV)/pytest -m "not performance"

test-unit:
	$(VENV)/pytest tests/unit -v

test-integration:
	$(VENV)/pytest tests/integration -v

test-e2e:
	$(VENV)/pytest tests/e2e -v -m e2e

test-adversarial:
	$(VENV)/pytest tests/adversarial -v

test-performance:
	$(VENV)/pytest tests/performance -v -m performance -s

run:
	ULI_MODE=local $(PY) -m uvicorn uli.api.app:app --host 0.0.0.0 --port 8080 --reload

demo:
	$(PY) scripts/demo.py

fetch-logs:
	$(PY) scripts/fetch_logs.py

collector:
	cd collector && go build -o ../bin/uli-collector ./cmd/uli-collector

airgap-bundle:
	bash deployment/airgap/build_bundle.sh

up:
	docker compose up --build

up-full:
	docker compose --profile ml --profile collector up --build

down:
	docker compose down -v
