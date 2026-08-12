#!/usr/bin/env sh
set -eu

if [ -z "${LKP_TEST_DATABASE_URL:-}" ]; then
  echo "LKP_TEST_DATABASE_URL must target a dedicated test database." >&2
  exit 2
fi

source_root=${1:-/workspace}
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
cp -R "$source_root"/. "$test_root"/
cd "$test_root"
export UV_PROJECT_ENVIRONMENT=/tmp/lkp-integration-venv
export PYTHONPATH="$test_root/services/api:$test_root/services/indexer"
uv run --frozen pytest tests/integration -q
