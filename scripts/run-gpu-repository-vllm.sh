#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"

"$repo_dir/scripts/check-repository-vllm-readiness.sh"

read_env() {
  local key="$1"
  sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | tr -d '\r'
}

runtime="$(read_env LKP_REPOSITORY_VLLM_RUNTIME)"
runtime="${runtime:-/home/kutae/.local/share/local-voice-agent/runtimes/vllm-0.25.1}"
model_file="$(read_env LKP_REPOSITORY_VLLM_MODEL_FILE)"
model_file="${model_file:-/mnt/e/AI/Models/HuggingFace/hub/local-shared-models/unsloth_Qwen3.6-27B-MTP-GGUF/Qwen3.6-27B-Q4_K_M.gguf}"
config_path="$(read_env LKP_REPOSITORY_VLLM_CONFIG_PATH)"
config_path="${config_path:-/mnt/e/Data/LocalKnowledgePortal/cache/model-configs/Qwen3.6-27B-text/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9}"
tokenizer_path="$(read_env LKP_REPOSITORY_VLLM_TOKENIZER_PATH)"
tokenizer_path="${tokenizer_path:-/mnt/e/AI/Models/HuggingFace/hub/models--Qwen--Qwen3.6-27B/snapshots/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9}"
port="$(read_env LKP_REPOSITORY_VLLM_PORT)"
port="${port:-18000}"
max_model_len="${LKP_REPOSITORY_VLLM_MAX_MODEL_LEN:-}"
if [[ -z "$max_model_len" ]]; then
  max_model_len="$(read_env LKP_REPOSITORY_VLLM_MAX_MODEL_LEN)"
fi
max_model_len="${max_model_len:-16384}"
gpu_utilization="${LKP_REPOSITORY_VLLM_GPU_MEMORY_UTILIZATION:-}"
if [[ -z "$gpu_utilization" ]]; then
  gpu_utilization="$(read_env LKP_REPOSITORY_VLLM_GPU_MEMORY_UTILIZATION)"
fi
gpu_utilization="${gpu_utilization:-0.90}"
max_num_batched_tokens="${LKP_REPOSITORY_VLLM_MAX_NUM_BATCHED_TOKENS:-}"
if [[ -z "$max_num_batched_tokens" ]]; then
  max_num_batched_tokens="$(
    read_env LKP_REPOSITORY_VLLM_MAX_NUM_BATCHED_TOKENS
  )"
fi
max_num_batched_tokens="${max_num_batched_tokens:-512}"
kv_cache_memory_bytes="${LKP_REPOSITORY_VLLM_KV_CACHE_MEMORY_BYTES:-}"
if [[ -z "$kv_cache_memory_bytes" ]]; then
  kv_cache_memory_bytes="$(read_env LKP_REPOSITORY_VLLM_KV_CACHE_MEMORY_BYTES)"
fi
hf_root="$(read_env LKP_HF_MODEL_ROOT)"
hf_root="${hf_root:-/mnt/e/AI/Models/HuggingFace}"

export HF_HOME="$hf_root"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_HUB_OFFLINE=1
# WSL2 on this workstation does not expose CUDA unified virtual addressing.
# vLLM's V2 runner requires UVA; the V1 runner supports the same Qwen3.6 MTP
# model without that host-memory requirement.
export VLLM_USE_V2_MODEL_RUNNER=0

cache_args=(--gpu-memory-utilization "$gpu_utilization")
if [[ -n "$kv_cache_memory_bytes" ]]; then
  cache_args=(--kv-cache-memory-bytes "$kv_cache_memory_bytes")
fi

exec "$runtime/.venv/bin/python" "$repo_dir/scripts/vllm-gguf-mtp-launcher.py" serve "$model_file" \
  --served-model-name qwen3.6-27b-mtp-q4-k-m \
  --tokenizer "$tokenizer_path" \
  --hf-config-path "$config_path" \
  --load-format gguf \
  --language-model-only \
  --reasoning-parser qwen3 \
  --speculative-config '{"method":"mtp","num_speculative_tokens":1}' \
  --no-enable-prefix-caching \
  --enforce-eager \
  --host 127.0.0.1 \
  --port "$port" \
  --max-model-len "$max_model_len" \
  --max-num-seqs 1 \
  --max-num-batched-tokens "$max_num_batched_tokens" \
  "${cache_args[@]}"
