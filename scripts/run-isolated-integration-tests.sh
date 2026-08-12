#!/usr/bin/env sh
# Run integration tests against a newly-created, disposable PostgreSQL database.
# The test database name is generated here; cleanup can only drop the database
# that this process successfully created. Production data is never a target.
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f "$repo_root/infra/docker/compose.wsl.yaml" \
    "$@"
}

test_db="lkp_test_verify_$(date +%Y%m%d%H%M%S)_$$"
created=0

cleanup() {
  if [ "$created" -eq 1 ]; then
    compose exec -T -e LKP_TEST_DATABASE_NAME="$test_db" postgres sh -lc '
      dropdb -U "$POSTGRES_USER" "$LKP_TEST_DATABASE_NAME"
    ' >/dev/null
  fi
}
trap cleanup EXIT HUP INT TERM

# A collision is not reused: createdb fails before `created` is set, so the
# cleanup trap cannot touch a pre-existing database.
compose exec -T -e LKP_TEST_DATABASE_NAME="$test_db" postgres sh -lc '
  createdb -U "$POSTGRES_USER" "$LKP_TEST_DATABASE_NAME"
' >/dev/null
created=1

# The running API image intentionally contains only production application
# files. Mount this repository read-only into a short-lived, private-network
# test container; its test helper copies that source into a temporary writable
# directory before installing/running test dependencies.
compose run --rm --no-deps \
  -v "$repo_root:/workspace:ro" \
  -e LKP_TEST_DATABASE_NAME="$test_db" \
  api sh /workspace/scripts/test-integration-service.sh

echo "integration_test_database=$test_db dropped_by_exit_trap=true"
