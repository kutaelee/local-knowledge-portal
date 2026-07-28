#!/usr/bin/env sh
# One-shot feed writer admitted by gpuq. The container uses the single Windows
# Ollama daemon and releases Gemma immediately after the bounded batch.
set -eu

docker compose \
  --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
  --profile manual-developer-feed \
  run --rm --no-deps developer-feed
