#!/usr/bin/env sh
# Run document and repository vector refresh, deduplication, and the semantic
# recovery probe with one model load inside one gpuq reservation.
set -eu

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.nightly_semantic_maintenance
