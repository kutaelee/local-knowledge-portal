#!/usr/bin/env sh
set -eu

source_root=${1:-/workspace}
validation_root=$(mktemp -d)
trap 'rm -rf "$validation_root"' EXIT
cp -R "$source_root"/. "$validation_root"/
cd "$validation_root"
export UV_PROJECT_ENVIRONMENT=/tmp/lkp-validation-venv
export PYTHONPATH="$validation_root/services/api:$validation_root/services/indexer"
uv run --frozen ruff check services scripts tests
uv run --frozen pytest tests/unit -q
