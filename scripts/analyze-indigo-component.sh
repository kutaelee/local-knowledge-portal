#!/usr/bin/env bash
# Run one IndigoESB component only as the argv workload of an admitted gpuq job.
set -euo pipefail

component="${1:-}"
case "$component" in
  esb)
    source_name="esb"
    ;;
  imc)
    source_name="imc"
    ;;
  agent)
    source_name="agent-ubuntu"
    ;;
  *)
    echo "usage: $0 {esb|imc|agent}" >&2
    exit 2
    ;;
esac

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"
compose_file="$repo_dir/infra/docker/compose.wsl.yaml"
source_root="/sources/windows-repositories/indigoesb/$source_name"
host_source_root="/mnt/c/Dev/Repos/indigoesb/$source_name"
evidence_root="/data/cache/repository-analysis/decompiled/projects/$component"
evidence_args=(--evidence-root ".decompiled=$evidence_root")
normalized_source=""
case "$component" in
  imc)
    normalized_source="$host_source_root/webapps/indigoesb/app.js"
    ;;
  agent)
    normalized_source="$host_source_root/adaptor/BACK/AD_TEMPLATE_RCV/AD_TEMPLATE_RCV.properties"
    ;;
esac
if [[ -n "$normalized_source" ]]; then
  normalized_sha="$(sha256sum "$normalized_source" | awk '{print $1}')"
  normalized_blob="$normalized_sha"
  if [[ "$component" == "imc" ]]; then
    normalized_blob="$normalized_sha-text-chunks-4096"
  fi
  normalized_host_root="/mnt/e/Data/LocalKnowledgePortal/cache/repository-analysis/normalized/blobs/$normalized_blob"
  normalized_container_root="/data/cache/repository-analysis/normalized/blobs/$normalized_blob"
  if [[ ! -f "$normalized_host_root/evidence-manifest.json" ]]; then
    echo "missing normalized evidence: $normalized_host_root" >&2
    exit 1
  fi
  evidence_args+=(--evidence-root ".normalized=$normalized_container_root")
fi
port="${LKP_REPOSITORY_VLLM_PORT:-18000}"
model_max_context="${LKP_REPOSITORY_VLLM_MAX_MODEL_LEN:-14000}"
model_max_output="${LKP_REPOSITORY_MODEL_MAX_OUTPUT:-2048}"
kv_cache_memory_bytes="${LKP_REPOSITORY_VLLM_KV_CACHE_MEMORY_BYTES:-1350000000}"
max_num_batched_tokens="${LKP_REPOSITORY_VLLM_MAX_NUM_BATCHED_TOKENS:-256}"
external_vllm="${LKP_REPOSITORY_EXTERNAL_VLLM:-false}"
model_name="${LKP_REPOSITORY_MODEL_NAME:-qwen3.6-27b-mtp-q4-k-m}"
model_quantization="${LKP_REPOSITORY_MODEL_QUANTIZATION:-Q4_K_M}"
export LKP_REPOSITORY_VLLM_MAX_MODEL_LEN="$model_max_context"
export LKP_REPOSITORY_VLLM_KV_CACHE_MEMORY_BYTES="$kv_cache_memory_bytes"
export LKP_REPOSITORY_VLLM_MAX_NUM_BATCHED_TOKENS="$max_num_batched_tokens"
result_dir="/mnt/e/Data/LocalKnowledgePortal/repository-analysis/results"
server_log_dir="/mnt/e/AI/Temp/local-knowledge-portal"
mkdir -p "$result_dir" "$server_log_dir"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
server_log="$server_log_dir/qwen36-mtp1-${component}-${run_stamp}.log"
result_file="$result_dir/${component}-${run_stamp}.json"
server_pid=""
if [[ "$external_vllm" == "true" ]]; then
  server_log="${LKP_REPOSITORY_EXTERNAL_VLLM_LOG:-$server_log}"
fi

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
  # The Windows-native server is intentionally loopback-only. WSL localhost
  # is a separate network namespace, so the application-container probe below
  # is the authoritative cross-runtime readiness check.
  ready=1
else
  for _ in $(seq 1 240); do
    if curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null; then
      ready=1
      break
    fi
    if [[ -n "$server_pid" ]] && ! kill -0 "$server_pid" 2>/dev/null; then
      break
    fi
    sleep 2
  done
fi
if [[ "$ready" != "1" ]]; then
  if [[ -f "$server_log" ]]; then
    tail -n 120 "$server_log" >&2
  fi
  echo "repository vLLM failed readiness for $component" >&2
  exit 1
fi

# Prove the application container can reach the loopback-only host model before
# starting a long analysis. No source content is sent by this probe.
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
  -e REPO_ANALYSIS_MODEL_INCLUDE_SOURCE_EXCERPTS=true \
  -e REPO_ANALYSIS_MODEL_MAX_SOURCE_CHARS=4500 \
  -e REPO_ANALYSIS_MODEL_TIMEOUT_SECONDS=300 \
  -e "REPO_ANALYSIS_MODEL_MAX_OUTPUT=$model_max_output" \
  -e REPO_ANALYSIS_RETRY_EXHAUSTED_CHECKPOINT_TASKS=true \
  -e REPO_ANALYSIS_DEFER_MODEL_EVALUATION=true \
  -v /mnt/c/Dev/Repos:/sources/windows-repositories:ro \
  api python -m lkp_indexer.repository_analysis \
  "$source_root" \
  --allowed-root "$source_root" \
  "${evidence_args[@]}" \
  --enable-local-model \
  --max-claims 10000 \
  --category "IndigoESB $component" \
  | tee "$result_file"

echo "analysis_result=$result_file"
