PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
UI := ui

.PHONY: install test lint fmt demo eval ui ui-install ui-test

install:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"

test: lint
	$(BIN)/pytest -q
	@if [ -d $(UI)/node_modules ]; then cd $(UI) && npm test --silent; else echo "UI tests skipped: run 'make ui-install' first"; fi

lint:
	$(BIN)/ruff format --check src tests
	$(BIN)/ruff check src tests

fmt:
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

demo:
	$(BIN)/cerebellum demo

eval:
	$(BIN)/cerebellum eval src/cerebellum/templates/refund/evals.yaml --mock

ui-install:
	cd $(UI) && npm ci

ui: ui-install
	cd $(UI) && npm run build

ui-test:
	cd $(UI) && npm test
