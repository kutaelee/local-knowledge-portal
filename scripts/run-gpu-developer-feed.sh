#!/usr/bin/env sh
# One-shot feed writer admitted by gpuq. The container uses the single Windows
# Ollama daemon and releases Gemma immediately after the bounded batch.
set -eu

LKP_REPO_PATH=/mnt/c/Dev/Repos/local-knowledge-portal docker compose \
  --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /mnt/c/Dev/Repos/local-knowledge-portal/infra/docker/compose.wsl.yaml \
  --profile manual-developer-feed \
  run --rm --no-deps developer-feed
