.PHONY: all install check backend mcp frontend format lint test tests test_watch integration_tests docker_tests help extended_tests

# Default target executed when no arguments are given to make.
all: help

# Use the ACTIVE interpreter (this project is managed with conda: `conda activate ERP_OPENCLAW`).
# Override per-invocation if needed:  make backend PYTHON=/path/to/python
PYTHON ?= python

# Intent-based install: resolves pyproject.toml ranges (and pulls mypy/ruff via .[dev]).
# For a byte-exact reproducible env, use the lock FIRST, then the editable install:
#   pip install -r requirements.txt && pip install -e '.[dev]'
# (the second command must NOT get --no-deps: mypy needs mypy_extensions/pathspec, so
#  --no-deps would install a broken mypy. Verified with `pip install --dry-run -e '.[dev]'`
#  that it adds only the dev tooling and leaves every pin in requirements.txt untouched.)
# Full procedure, prerequisites and known env pitfalls: see ./venv.txt
install:
	$(PYTHON) -m pip install -e '.[dev]'
	cd frontend && npm ci

check:
	$(PYTHON) scripts/check_environment.py

backend:
	PYTHONPATH=src $(PYTHON) -m uvicorn api_view.web_main:app --reload --host 0.0.0.0 --port 8090

mcp:
	PYTHONPATH=src $(PYTHON) -m mcp_server.server_main

frontend:
	cd frontend && npm run dev

TEST_FILE ?= tests

test:
	PYTHONPATH=src $(PYTHON) -m pytest $(TEST_FILE)

integration_tests:
	$(PYTHON) -m pytest tests/integration_tests

test_watch:
	$(PYTHON) -m ptw --snapshot-update --now . -- -vv tests/unit_tests

test_profile:
	$(PYTHON) -m pytest -vv tests/unit_tests/ --profile-svg

extended_tests:
	$(PYTHON) -m pytest --only-extended $(TEST_FILE)


######################
# LINTING AND FORMATTING
######################

# Define a variable for Python and notebook files.
PYTHON_FILES=src/
MYPY_CACHE=.mypy_cache
lint format: PYTHON_FILES=.
lint_diff format_diff: PYTHON_FILES=$(shell git diff --name-only --diff-filter=d main | grep -E '\.py$$|\.ipynb$$')
lint_package: PYTHON_FILES=src
lint_tests: PYTHON_FILES=tests
lint_tests: MYPY_CACHE=.mypy_cache_test

lint lint_diff lint_package lint_tests:
	$(PYTHON) -m ruff check .
	[ "$(PYTHON_FILES)" = "" ] || $(PYTHON) -m ruff format $(PYTHON_FILES) --diff
	[ "$(PYTHON_FILES)" = "" ] || $(PYTHON) -m ruff check --select I $(PYTHON_FILES)
	[ "$(PYTHON_FILES)" = "" ] || $(PYTHON) -m mypy --strict $(PYTHON_FILES)
	[ "$(PYTHON_FILES)" = "" ] || mkdir -p $(MYPY_CACHE) && $(PYTHON) -m mypy --strict $(PYTHON_FILES) --cache-dir $(MYPY_CACHE)

format format_diff:
	$(PYTHON) -m ruff format $(PYTHON_FILES)
	$(PYTHON) -m ruff check --select I --fix $(PYTHON_FILES)

spell_check:
	codespell --toml pyproject.toml

spell_fix:
	codespell --toml pyproject.toml -w

######################
# HELP
######################

help:
	@echo '----'
	@echo 'install                      - install Python and frontend dependencies'
	@echo 'check                        - check environment variables and services'
	@echo 'mcp                          - start ERP MCP server'
	@echo 'backend                      - start FastAPI backend'
	@echo 'frontend                     - start Vue development server'
	@echo 'format                       - run code formatters'
	@echo 'lint                         - run linters'
	@echo 'test                         - run unit tests'
	@echo 'tests                        - run unit tests'
	@echo 'test TEST_FILE=<test_file>   - run all tests in file'
	@echo 'test_watch                   - run unit tests in watch mode'
