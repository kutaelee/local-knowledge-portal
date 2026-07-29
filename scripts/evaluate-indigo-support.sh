#!/usr/bin/env bash
# Run only as the argv workload of an admitted gpuq reservation.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"
compose_file="$repo_dir/infra/docker/compose.wsl.yaml"
package="${1:-/data/repository-analysis/results/indigoesb-support-retrieval.json}"
port="${LKP_REPOSITORY_VLLM_PORT:-18000}"
external_vllm="${LKP_REPOSITORY_EXTERNAL_VLLM:-false}"
model_name="${LKP_REPOSITORY_MODEL_NAME:-qwen3.6-27b-mtp-q4-k-m}"
model_quantization="${LKP_REPOSITORY_MODEL_QUANTIZATION:-Q4_K_M}"
model_max_context="${LKP_REPOSITORY_VLLM_MAX_MODEL_LEN:-16384}"
log_dir="/mnt/e/AI/Temp/local-knowledge-portal"
mkdir -p "$log_dir"
server_log="$(mktemp "$log_dir/qwen36-support-evaluation.XXXXXX.log")"
server_pid=""

cleanup() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill -TERM "$server_pid" 2>/dev/null || true
    for _ in $(seq 1 30); do
      kill -0 "$server_pid" 2>/dev/null || break
      sleep 1
    done
  fi
}
trap cleanup EXIT INT TERM

if [[ "$external_vllm" != "true" ]]; then
  "$repo_dir/scripts/check-repository-vllm-readiness.sh"
  "$repo_dir/scripts/run-gpu-repository-vllm.sh" >"$server_log" 2>&1 &
  server_pid=$!
fi

ready=0
if [[ "$external_vllm" == "true" ]]; then
  # The Windows-native server is loopback-only and WSL localhost is a
  # separate namespace. The application-container probe below is authoritative.
  ready=1
else
  for _ in $(seq 1 240); do
    if curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null; then
      ready=1
      break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
      break
    fi
    sleep 2
  done
fi
if [[ "$ready" != "1" ]]; then
  tail -n 120 "$server_log" >&2
  echo "repository vLLM failed readiness for support evaluation" >&2
  exit 1
fi

# Prove that the evaluation container can reach the admitted host model before
# sending any retrieval package.
docker compose \
  --env-file "$env_file" \
  -f "$compose_file" \
  run --rm --no-deps \
  api python -c \
  "import urllib.request; urllib.request.urlopen('http://host.docker.internal:${port}/health', timeout=5)"

docker compose \
  --env-file "$env_file" \
  -f "$compose_file" \
  run --rm --no-deps \
  -e REPO_ANALYSIS_MODEL_ENABLED=true \
  -e "REPO_ANALYSIS_MODEL_BASE_URL=http://host.docker.internal:${port}/v1" \
  -e "REPO_ANALYSIS_MODEL_NAME=$model_name" \
  -e "REPO_ANALYSIS_MODEL_QUANTIZATION=$model_quantization" \
  -e REPO_ANALYSIS_MODEL_MAX_CONCURRENCY=1 \
  -e "REPO_ANALYSIS_MODEL_MAX_CONTEXT=$model_max_context" \
  -e REPO_ANALYSIS_MODEL_TIMEOUT_SECONDS=300 \
  -e REPO_ANALYSIS_MODEL_MAX_OUTPUT=2048 \
  api python -m lkp_indexer.repository_retrieval_evaluation answer \
  --package "$package"
