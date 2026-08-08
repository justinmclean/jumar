# SPDX-License-Identifier: Apache-2.0
# One command per gate. If `make check` passes locally, CI passes.
PY := python3

.DEFAULT_GOAL := help
.PHONY: help install check test cov lint types loop-test clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install the package with dev extras
	$(PY) -m pip install -e ".[dev]"

check: lint types cov loop-test  ## Everything CI runs

test:  ## Run the test suite
	$(PY) -m pytest -q

cov:  ## Run the suite with coverage
	$(PY) -m pytest -q --cov=getstuffdone --cov-report=term-missing

lint:  ## ruff
	ruff check src tests tools
	ruff format --check src tests

types:  ## mypy
	mypy src/getstuffdone

loop-test:  ## Syntax + fixture tests for the build loop itself
	bash -n tools/spec-loop/loop.sh tools/spec-loop/lib.sh
	bash tools/spec-loop/tests/test_runner_fixtures.sh

clean:  ## Remove caches and coverage artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
