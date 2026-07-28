#!/usr/bin/env sh
# Called only by the gpuq wrapper. The one-shot application uses the
# workstation-wide Windows Ollama and never starts a second daemon.
set -eu

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.embedding_recovery_probe
