#!/usr/bin/env sh
set -eu

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

# gpuq owns admission. This uses the workstation-wide Windows Ollama daemon.
compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.repository_embedding_reindex "$@"
