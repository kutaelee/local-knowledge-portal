#!/usr/bin/env bash
# Run only as the argv workload of an admitted gpuq reservation.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
port="${LKP_REPOSITORY_VLLM_PORT:-18000}"
log_dir="/mnt/e/AI/Temp/local-knowledge-portal"
mkdir -p "$log_dir"
log_file="$(mktemp "$log_dir/qwen36-mtp1-qualification.XXXXXX.log")"
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

"$repo_dir/scripts/run-gpu-repository-vllm.sh" >"$log_file" 2>&1 &
server_pid=$!

ready=0
for _ in $(seq 1 180); do
  if curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/health" >/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep 2
done

if [[ "$ready" != "1" ]]; then
  tail -n 120 "$log_file" >&2
  echo "repository vLLM failed readiness" >&2
  exit 1
fi

response="$(
  curl --fail --silent --max-time 180 \
    -H "Content-Type: application/json" \
    -d '{
      "model":"qwen3.6-27b-mtp-q4-k-m",
      "messages":[
        {
          "role":"system",
          "content":"Return only a JSON object. Do not infer facts that are not supplied."
        },
        {
          "role":"user",
          "content":"No repository evidence is supplied. Return empty arrays named claims, contradictions, and missing_knowledge."
        }
      ],
      "temperature":0,
      "max_tokens":256,
      "chat_template_kwargs":{"enable_thinking":false},
      "response_format":{
        "type":"json_schema",
        "json_schema":{
          "name":"repository_vllm_qualification",
          "strict":true,
          "schema":{
            "type":"object",
            "properties":{
              "claims":{"type":"array","items":{"type":"string"},"maxItems":0},
              "contradictions":{"type":"array","items":{"type":"string"},"maxItems":0},
              "missing_knowledge":{"type":"array","items":{"type":"string"},"maxItems":0}
            },
            "required":["claims","contradictions","missing_knowledge"],
            "additionalProperties":false
          }
        }
      }
    }' \
    "http://127.0.0.1:${port}/v1/chat/completions"
)"

QUALIFICATION_RESPONSE="$response" python3 - <<'PY'
import json
import os
import sys

body = json.loads(os.environ["QUALIFICATION_RESPONSE"])
message = body["choices"][0]["message"]
try:
    content = json.loads(message["content"])
except (TypeError, json.JSONDecodeError):
    print(
        json.dumps(
            {
                "finish_reason": body["choices"][0].get("finish_reason"),
                "content_preview": (message.get("content") or "")[:500],
                "reasoning_preview": (message.get("reasoning_content") or "")[:500],
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    raise
required = {"claims", "contradictions", "missing_knowledge"}
if not required <= content.keys():
    raise SystemExit(f"missing structured keys: {sorted(required - content.keys())}")
if not all(isinstance(content[key], list) for key in required):
    raise SystemExit("qualification fields must all be arrays")
if set(content) != required:
    raise SystemExit(f"unexpected structured keys: {sorted(set(content) - required)}")
if any(content[key] for key in required):
    raise SystemExit(f"qualification arrays must be empty: {content!r}")
if body.get("model") != "qwen3.6-27b-mtp-q4-k-m":
    raise SystemExit(f"unexpected served model: {body.get('model')!r}")
usage = body.get("usage") or {}
print(
    json.dumps(
        {
            "status": "qualified",
            "model": body.get("model"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "structured_keys": sorted(required),
        },
        ensure_ascii=False,
    )
)
PY
