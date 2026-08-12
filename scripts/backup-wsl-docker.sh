#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
compose_file="$repo_root/infra/docker/compose.wsl.yaml"
env_file=/mnt/c/Docker/local-knowledge-portal/.env
backup_root=/mnt/d/LocalBackup/LocalKnowledgePortal
stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
database_dir="$backup_root/database/$stamp"
config_dir="$backup_root/config/$stamp"
manifest_dir="$backup_root/manifests/$stamp"
vault_dir="$backup_root/vault/$stamp"
dump_path="$database_dir/lkp.dump"

if [[ -e "$database_dir" || -e "$config_dir" || -e "$manifest_dir" || -e "$vault_dir" ]]; then
  echo "Immutable backup destination already exists: $stamp" >&2
  exit 1
fi

POSTGRES_USER=$(sed -n 's/^POSTGRES_USER=//p' "$env_file" | tr -d '\r')
POSTGRES_DB=$(sed -n 's/^POSTGRES_DB=//p' "$env_file" | tr -d '\r')
if [[ -z "$POSTGRES_USER" || -z "$POSTGRES_DB" ]]; then
  echo "POSTGRES_USER or POSTGRES_DB is missing from $env_file" >&2
  exit 1
fi
mkdir -p "$database_dir" "$config_dir" "$manifest_dir" "$vault_dir"
printf '{"status":"in_progress","timestamp":"%s"}\n' "$stamp" \
  > "$manifest_dir/status.json"
trap 'printf "{\"status\":\"failed\",\"timestamp\":\"%s\"}\n" "$stamp" \
  > "$manifest_dir/status.json"' ERR

docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom > "$dump_path"
sha256sum "$dump_path" > "$manifest_dir/database.sha256"
cp -R /mnt/c/Docker/local-knowledge-portal/config/. "$config_dir/"
if [[ -d /mnt/e/Data/LocalKnowledgePortal/vault/_generated ]]; then
  cp -R /mnt/e/Data/LocalKnowledgePortal/vault/_generated "$vault_dir/"
fi
if [[ -d /mnt/e/Data/LocalKnowledgePortal/ingest/codex-spool ]]; then
  mkdir -p "$manifest_dir/events"
  cp -R /mnt/e/Data/LocalKnowledgePortal/ingest/codex-spool \
    "$manifest_dir/events/codex-spool"
fi
if compgen -G '/mnt/e/Manifests/local-knowledge-portal-*.json' >/dev/null; then
  mkdir -p "$manifest_dir/models"
  cp /mnt/e/Manifests/local-knowledge-portal-*.json "$manifest_dir/models/"
fi

revision=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select version_num from alembic_version")
database_version=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "show server_version")
embedding_provider=$(sed -n 's/^LKP_EMBEDDING_PROVIDER=//p' "$env_file" | tr -d '\r')
embedding_model=$(sed -n 's/^LKP_EMBEDDING_MODEL=//p' "$env_file" | tr -d '\r')
embedding_model_digest=$(sed -n 's/^LKP_EMBEDDING_MODEL_DIGEST=//p' "$env_file" | tr -d '\r')
embedding_dimension=$(sed -n 's/^LKP_EMBEDDING_DIMENSION=//p' "$env_file" | tr -d '\r')
embedding_revision=$(sed -n 's/^LKP_EMBEDDING_REVISION=//p' "$env_file" | tr -d '\r')
pipeline_version=$(sed -n 's/^LKP_PIPELINE_VERSION=//p' "$env_file" | tr -d '\r')
generation_provider=$(sed -n 's/^LKP_GENERATION_PROVIDER=//p' "$env_file" | tr -d '\r')
generation_model=$(sed -n 's/^LKP_GENERATION_MODEL=//p' "$env_file" | tr -d '\r')
generation_model_digest=$(sed -n 's/^LKP_GENERATION_MODEL_DIGEST=//p' "$env_file" | tr -d '\r')
model="$embedding_model $embedding_model_digest; $generation_model $generation_model_digest"
generation_prompt_version=$(sed -n 's/^LKP_GENERATION_PROMPT_VERSION=//p' "$env_file" | tr -d '\r')
generation_prompt_version=${generation_prompt_version:-evidence-blog-v9}
generation_temperature=$(sed -n 's/^LKP_GENERATION_TEMPERATURE=//p' "$env_file" | tr -d '\r')
generation_temperature=${generation_temperature:-0}
generation_context_window=$(sed -n 's/^LKP_GENERATION_CONTEXT_WINDOW=//p' "$env_file" | tr -d '\r')
generation_context_window=${generation_context_window:-16384}
generation_keep_alive=$(sed -n 's/^LKP_GENERATION_KEEP_ALIVE=//p' "$env_file" | tr -d '\r')
generation_keep_alive=${generation_keep_alive:-2m}
curator_state=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select coalesce((select value::text from system_setting
   where key='knowledge_curator.scheduler'), '{}')")
dump_size=$(stat -c %s "$dump_path")
dump_sha=$(awk '{print $1}' "$manifest_dir/database.sha256")
source_hash=$(sha256sum \
  /mnt/c/Docker/local-knowledge-portal/config/source-roots.yaml | awk '{print $1}')
managed_vault_files=$(find "$vault_dir" -type f | wc -l)
spool_files=$(find "$manifest_dir/events" -type f 2>/dev/null | wc -l || true)

python3 - "$manifest_dir/manifest.json" <<PY
import json
import sys
from datetime import datetime, timezone

manifest = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "runtime": "WSL2 Docker Desktop",
    "database_version": ${database_version@Q},
    "schema_revision": ${revision@Q},
    "dump_size": int(${dump_size@Q}),
    "sha256": ${dump_sha@Q},
    "source_configuration_hash": ${source_hash@Q},
    "model": ${model@Q},
    "embedding_provider": ${embedding_provider@Q},
    "embedding_model": ${embedding_model@Q},
    "embedding_model_digest": ${embedding_model_digest@Q},
    "embedding_dimension": int(${embedding_dimension@Q}),
    "embedding_revision": ${embedding_revision@Q},
    "pipeline_version": ${pipeline_version@Q},
    "generation_provider": ${generation_provider@Q},
    "generation_model": ${generation_model@Q},
    "generation_model_digest": ${generation_model_digest@Q},
    "generation_prompt_version": ${generation_prompt_version@Q},
    "generation_parameters": {
        "temperature": float(${generation_temperature@Q}),
        "context_window": int(${generation_context_window@Q}),
        "keep_alive": ${generation_keep_alive@Q},
    },
    "knowledge_curator_state": json.loads(${curator_state@Q}),
    "compose_path": ${compose_file@Q},
    "data_path": "E:\\\\Data\\\\LocalKnowledgePortal",
    "managed_vault_files": int(${managed_vault_files@Q}),
    "spool_files": int(${spool_files@Q}),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, ensure_ascii=False, indent=2)
    handle.write("\\n")
PY

docker compose --env-file "$env_file" -f "$compose_file" exec -T api \
  python -m lkp.record_backup \
  --path "D:\\LocalBackup\\LocalKnowledgePortal\\database\\$stamp" \
  --manifest "/backups/manifests/$stamp/manifest.json" >/dev/null

printf '{"status":"succeeded","timestamp":"%s"}\n' "$stamp" \
  > "$manifest_dir/status.json"
trap - ERR
echo "$database_dir"
