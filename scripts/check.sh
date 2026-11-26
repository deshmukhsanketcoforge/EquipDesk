#!/bin/sh
# Full local/CI gate: lint, format, types, tests with coverage.
# Run via `docker compose run --rm test` (or `make test`).
set -eu

echo "==> ruff check"
ruff check .
echo "==> ruff format --check"
ruff format --check .
echo "==> mypy"
mypy
echo "==> pytest"
pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=90 "$@"
