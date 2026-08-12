#!/usr/bin/env sh
# One-shot feed writer admitted by gpuq. The container uses the canonical WSL
# source, the single Windows Ollama daemon, and releases Gemma after the batch.
set -eu

container=local-knowledge-portal-manual-feed

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM
cleanup
docker compose \
  --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
  --profile manual-developer-feed \
  run --rm --no-deps \
  --name "$container" \
  developer-feed
