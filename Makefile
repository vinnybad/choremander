.DEFAULT_GOAL := help
SHELL := /bin/bash

VENV    := .venv
PY      := $(VENV)/bin/python
PYTEST  := $(PY) -m pytest
RUFF    := $(VENV)/bin/ruff
PW      := e2e/node_modules/.bin/playwright
COMPOSE := docker compose -f docker-compose.dev.yml

# Python 3.13 to match the Home Assistant core the harness pins.
SYS_PYTHON ?= python3.13

.PHONY: help install lint lint-src format format-src test cov up down restart reset seed logs shell e2e browser browser-headed validate verify clean

help: ## Show this help
	@echo "Choremander development commands"
	@echo
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Typical loop:  make up  ->  edit  ->  make verify"

# ---------------------------------------------------------------- setup

install: ## Create the venv and install all Python and Node test dependencies
	@command -v $(SYS_PYTHON) >/dev/null || { \
		echo "$(SYS_PYTHON) not found. Install Python 3.13 (brew install python@3.13)."; exit 1; }
	$(SYS_PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements-test.txt
	cd e2e && npm install --silent && ./node_modules/.bin/playwright install chromium
	@echo "Ready. Run 'make up' to start Home Assistant, then 'make verify'."

# ---------------------------------------------------------------- static

# Gated on tests/ only. The integration source predates this suite and carries
# ~34 pre-existing findings; reformatting it is a separate, deliberate cleanup
# (see `lint-src` / `format-src`) rather than something `make verify` forces.
lint: ## Check formatting and lint rules on the test suite
	$(RUFF) check tests
	$(RUFF) format --check tests

format: ## Apply formatting and autofixable lint rules to the test suite
	$(RUFF) check --fix tests
	$(RUFF) format tests

lint-src: ## Report lint findings in the integration source (advisory)
	-$(RUFF) check custom_components
	-$(RUFF) format --check custom_components

format-src: ## Reformat the integration source - large diff, run deliberately
	$(RUFF) check --fix custom_components
	$(RUFF) format custom_components

# ---------------------------------------------------------------- tests

test: ## Run the fast in-process unit tests (no container needed)
	$(PYTEST) -m "not e2e"

cov: ## Unit tests with a coverage report
	$(PYTEST) -m "not e2e" --cov=custom_components/choremander --cov-report=term-missing

e2e: ## Run the API-driven end-to-end tests against the running container
	$(PYTEST) -m e2e

browser: ## Run the Playwright tests against the running container
	cd e2e && ../$(PW) test

browser-headed: ## Same as `browser`, but watch it happen in a real browser
	cd e2e && ../$(PW) test --headed

validate: ## Run Home Assistant's hassfest checks the way CI does
	docker run --rm -v "$(PWD)":/github/workspace ghcr.io/home-assistant/hassfest:latest

verify: lint test up e2e browser ## Everything: lint, unit, container, API and browser tests
	@echo
	@echo "All checks passed."

# ---------------------------------------------------------------- env

up: ## Start Home Assistant and wait until Choremander has loaded
	./dev/start.sh

down: ## Stop the container
	./dev/stop.sh

restart: ## Restart to pick up Python changes, then wait until it is serving
	./dev/restart.sh

seed: ## Restore Choremander's data to the committed fixture
	./dev/seed.sh

reset: ## Wipe all generated state and start from a fresh-clone baseline
	./dev/reset.sh

logs: ## Follow filtered container logs
	./dev/logs.sh

shell: ## Open a shell inside the container
	./dev/shell.sh

clean: ## Remove test artifacts and caches
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov
	rm -rf e2e/test-results e2e/playwright-report e2e/.auth
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
