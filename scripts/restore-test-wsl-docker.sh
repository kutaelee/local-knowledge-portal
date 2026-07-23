#!/usr/bin/env bash
set -euo pipefail

backup_root=/mnt/d/LocalBackup/LocalKnowledgePortal/database
backup_dir=${1:-$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)}
dump_path="$backup_dir/lkp.dump"
container="lkp-restore-validation-$$"

if [[ ! -f "$dump_path" ]]; then
  echo "Backup dump not found: $dump_path" >&2
  exit 1
fi

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$container" \
  --tmpfs /var/lib/postgresql:rw \
  -e POSTGRES_DB=restore_validation \
  -e POSTGRES_USER=restore_validation \
  -e POSTGRES_PASSWORD=restore_validation_only \
  pgvector/pgvector:0.8.2-pg18-trixie@sha256:b7337db8fe39d12fe8ecb0003c72680f24479813a744b43154eee6f2eab5a5f3 \
  >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$container" pg_isready -U restore_validation -d restore_validation \
    >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$container" pg_isready -U restore_validation -d restore_validation >/dev/null
docker exec -i "$container" pg_restore \
  -U restore_validation -d restore_validation --no-owner --no-privileges < "$dump_path"

docker exec "$container" psql -U restore_validation -d restore_validation -Atc \
  "select json_build_object(
    'revision', (select version_num from alembic_version),
    'documents', (select count(*) from document),
    'chunks', (select count(*) from document_chunk),
    'vectors', (select count(*) from chunk_embedding),
    'activities', (select count(*) from activity_event)
  );"
