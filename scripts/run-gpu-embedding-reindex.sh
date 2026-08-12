#!/usr/bin/env sh
set -eu

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

# gpuq owns admission for this command. The one-shot container reaches the
# workstation-wide Windows Ollama through host.docker.internal; it must never
# start a second Ollama daemon or write a second active model store.
compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.embedding_reindex "$@"
