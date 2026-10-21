.PHONY: install run test lint fmt openapi compose-up compose-down

PY ?= .venv/bin/python

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

run:
	$(PY) -m uvicorn kyc.main:app --reload --port 8000

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

openapi:
	$(PY) scripts/export_openapi.py

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down -v
