#!/usr/bin/env sh
# Host gpuq invokes this only after it terminates a managed portal workload.
set -eu

service=${1:-}
case "$service" in
  ollama-generation|ollama-embedding-batch)
    ;;
  *)
    echo "refusing unknown managed Ollama service: $service" >&2
    exit 2
    ;;
esac

docker compose \
  --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
  --profile manual-embedding \
  stop "$service"
