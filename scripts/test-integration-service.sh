#!/usr/bin/env sh
set -eu

case "${LKP_TEST_DATABASE_NAME:-}" in
  lkp_test_*) ;;
  *)
    echo "LKP_TEST_DATABASE_NAME must start with lkp_test_." >&2
    exit 2
    ;;
esac

if [ -z "${LKP_DATABASE_URL:-}" ]; then
  echo "LKP_DATABASE_URL is required to derive the isolated test connection." >&2
  exit 2
fi

export LKP_TEST_DATABASE_URL="${LKP_DATABASE_URL%/*}/$LKP_TEST_DATABASE_NAME"
exec sh /workspace/scripts/test-integration-container.sh /workspace
