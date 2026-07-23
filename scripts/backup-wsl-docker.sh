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
if [[ -d /mnt/e/LocalKnowledgePortal/ingest/codex-spool ]]; then
  mkdir -p "$manifest_dir/events"
  cp -R /mnt/e/LocalKnowledgePortal/ingest/codex-spool \
    "$manifest_dir/events/codex-spool"
fi

revision=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select version_num from alembic_version")
database_version=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "show server_version")
model=$(docker compose --env-file "$env_file" -f "$compose_file" exec -T ollama \
  ollama list | awk 'NR==2 {print $1 " " $2 " " $3}')
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
