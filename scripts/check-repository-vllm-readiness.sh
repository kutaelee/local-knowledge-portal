#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"
expected_size="17106773120"
default_model="/mnt/e/AI/Models/HuggingFace/hub/local-shared-models/unsloth_Qwen3.6-27B-MTP-GGUF/Qwen3.6-27B-Q4_K_M.gguf"
default_runtime="/home/kutae/.local/share/local-voice-agent/runtimes/vllm-0.25.1"
default_config="/mnt/e/Data/LocalKnowledgePortal/cache/model-configs/Qwen3.6-27B-text/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9"
default_tokenizer="/mnt/e/AI/Models/HuggingFace/hub/models--Qwen--Qwen3.6-27B/snapshots/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9"

read_env() {
  local key="$1"
  if [[ -f "$env_file" ]]; then
    sed -n "s/^${key}=//p" "$env_file" | tail -n 1 | tr -d '\r'
  fi
}

model_file="$(read_env LKP_REPOSITORY_VLLM_MODEL_FILE)"
model_file="${model_file:-$default_model}"
runtime="$(read_env LKP_REPOSITORY_VLLM_RUNTIME)"
runtime="${runtime:-$default_runtime}"
config_path="$(read_env LKP_REPOSITORY_VLLM_CONFIG_PATH)"
config_path="${config_path:-$default_config}"
tokenizer_path="$(read_env LKP_REPOSITORY_VLLM_TOKENIZER_PATH)"
tokenizer_path="${tokenizer_path:-$default_tokenizer}"
vllm_bin="$runtime/.venv/bin/vllm"
runtime_python="$runtime/.venv/bin/python"
failures=0
warnings=0

check() {
  local label="$1"
  shift
  if "$@"; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label"
    failures=$((failures + 1))
  fi
}

warn() {
  printf 'WARN %s\n' "$1"
  warnings=$((warnings + 1))
}

check "environment file exists" test -f "$env_file"
check "Qwen3.6 27B Q4_K_M model exists" test -f "$model_file"
if [[ -f "$model_file" ]]; then
  actual_size="$(stat -c %s "$model_file")"
  check "model size matches canonical artifact" test "$actual_size" = "$expected_size"
fi
check "workstation vLLM executable exists" test -x "$vllm_bin"
if [[ -x "$vllm_bin" ]]; then
  version="$("$runtime_python" -c 'import vllm; print(vllm.__version__)')"
  check "vLLM version is 0.25.1" test "$version" = "0.25.1"
  plugin_version="$("$runtime_python" -c \
    'from importlib.metadata import version; print(version("vllm-gguf-plugin"))' \
    2>/dev/null || true)"
  check "vLLM GGUF plugin version is 0.0.4" test "$plugin_version" = "0.0.4"
  gguf_version="$("$runtime_python" -c \
    'from importlib.metadata import version; print(version("gguf"))' \
    2>/dev/null || true)"
  check "GGUF metadata package version is 0.19.0" test "$gguf_version" = "0.19.0"
  check "Qwen3.5/3.6 MTP implementation exists" \
    test -f "$runtime/.venv/lib/python3.12/site-packages/vllm/model_executor/models/qwen3_5_mtp.py"
  runtime_site="$runtime/.venv/lib/python3.12/site-packages"
  check "Qwen3.6 text architecture is registered for engine subprocesses" \
    grep -qF '"Qwen3_5ForCausalLM": ("qwen3_5", "Qwen3_5ForCausalLM")' \
    "$runtime_site/vllm/model_executor/models/registry.py"
  check "Qwen3.6 text embeddings receive the GGUF quantization config" \
    grep -qF 'prefix=maybe_prefix(prefix, "embed_tokens")' \
    "$runtime_site/vllm/model_executor/models/qwen3_5.py"
  check "Qwen3.6 MTP embeddings receive the GGUF quantization config" \
    grep -qF 'prefix=maybe_prefix(prefix, "embed_tokens")' \
    "$runtime_site/vllm/model_executor/models/qwen3_5_mtp.py"
  check "GGUF fused qweight types use their dedicated shard store" \
    grep -qF 'param._store(loaded_weight, loaded_shard_id)' \
    "$runtime_site/vllm_gguf_plugin/quantization/params.py"
  check "mixed GGUF quant types are validated against packed row width" \
    grep -qF '_resolve_quant_type_from_row_width' \
    "$runtime_site/vllm_gguf_plugin/quantization/linear.py"
  check "Qwen3.6 GGUF Conv1d kernels restore the vLLM group dimension" \
    grep -qF 'linear_attn\.conv1d\.weight' \
    "$runtime_site/vllm_gguf_plugin/weights_adapter/default.py"
  check "Qwen3.6 text configs are recognized by the MTP converter" \
    grep -qF '"qwen3_5_text",' \
    "$runtime_site/vllm/config/speculative.py"
  check "Qwen3.6 text model receives hybrid Mamba cache defaults" \
    grep -qF '"Qwen3_5ForCausalLM": Qwen3_5ForConditionalGenerationConfig' \
    "$runtime_site/vllm/model_executor/models/config.py"
  check "Qwen3.6 hybrid Mamba config initializes cache block sizing" \
    grep -qF 'HybridAttentionMambaModelConfig.verify_and_update_config(vllm_config)' \
    "$runtime_site/vllm/model_executor/models/config.py"
  check "Qwen3.6 text-only models provide three-axis M-RoPE positions" \
    "$runtime_python" -c \
    'from vllm.model_executor.models.interfaces import supports_mrope; from vllm.model_executor.models.qwen3_5 import Qwen3_5ForCausalLM; m = Qwen3_5ForCausalLM.__new__(Qwen3_5ForCausalLM); p, d = m.get_mrope_input_positions([1, 2, 3], []); assert supports_mrope(m) and p.tolist() == [[0, 1, 2]] * 3 and d == 0'
  check "repository requests disable thinking for bounded JSON extraction" \
    grep -qF '"chat_template_kwargs": {"enable_thinking": False}' \
    "$repo_dir/services/indexer/lkp_indexer/repository_analysis/provider.py"
  check "repository requests use strict JSON Schema constrained decoding" \
    grep -qF '"type": "json_schema"' \
    "$repo_dir/services/indexer/lkp_indexer/repository_analysis/provider.py"
  check "hybrid Mamba pages align to the attention page granularity" \
    grep -qF 'attention_page_sizes' \
    "$runtime_site/vllm/v1/core/kv_cache_utils.py"
  check "quantized target embeddings can be shared with the MTP proposer" \
    grep -qF 'getattr(target_embed_tokens, "weight", None)' \
    "$runtime_site/vllm/v1/spec_decode/llm_base_proposer.py"
fi

gpu_memory="$(
  nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
    | head -n 1 \
    | tr -d ' '
)"
if [[ "$gpu_memory" =~ ^[0-9]+$ ]] && (( gpu_memory >= 30000 )); then
  printf 'PASS GPU memory is %s MiB\n' "$gpu_memory"
else
  printf 'FAIL at least 30000 MiB GPU memory is required (found %s)\n' "${gpu_memory:-unknown}"
  failures=$((failures + 1))
fi

check "derived text-only model config exists" test -f "$config_path/config.json"
check "immutable base tokenizer snapshot exists" test -f "$tokenizer_path/tokenizer.json"
if find "$tokenizer_path" -maxdepth 1 -type f \
  \( -name '*.safetensors' -o -name '*.bin' -o -name '*.pt' \) \
  -print -quit | grep -q .; then
  printf 'FAIL tokenizer snapshot must not contain duplicate weights\n'
  failures=$((failures + 1))
else
  printf 'PASS tokenizer snapshot contains no model weights\n'
fi

enabled="$(read_env REPO_ANALYSIS_MODEL_ENABLED)"
if [[ "${enabled,,}" == "true" ]]; then
  warn "repository model is enabled in the operational env; keep it false until qualification passes"
else
  printf 'PASS operational repository model remains disabled\n'
fi

printf 'SUMMARY failures=%s warnings=%s\n' "$failures" "$warnings"
(( failures == 0 ))
