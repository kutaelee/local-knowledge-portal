#!/usr/bin/env sh
# One gpuq-owned nightly pipeline. GPUQ admission waits behind earlier work;
# stages run sequentially and exact one-shot containers are recoverable.
set -u

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

containers="
local-knowledge-portal-nightly-semantic
local-knowledge-portal-nightly-generation
local-knowledge-portal-nightly-embedding-refresh
local-knowledge-portal-nightly-feed
"

cleanup() {
  for container in $containers; do
    docker rm -f "$container" >/dev/null 2>&1 || true
  done
}

stage() {
  name=$1
  shift
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  printf '{"event":"stage_started","stage":"%s","at":"%s"}\n' "$name" "$started"
  if "$@"; then
    finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf '{"event":"stage_succeeded","stage":"%s","at":"%s"}\n' "$name" "$finished"
    return 0
  else
    code=$?
    finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf '{"event":"stage_failed","stage":"%s","at":"%s","exit_code":%s}\n' \
      "$name" "$finished" "$code"
    return "$code"
  fi
}

trap cleanup EXIT INT TERM
cleanup
failures=0

stage semantic-maintenance \
  compose --profile manual-embedding run --rm --no-deps \
    --name local-knowledge-portal-nightly-semantic \
    embedding-reindex python -m lkp_indexer.nightly_semantic_maintenance ||
  failures=$((failures + 1))

stage generation-maintenance \
  compose --profile manual-curation run --rm --no-deps \
    --name local-knowledge-portal-nightly-generation \
    knowledge-curator python -m lkp_indexer.nightly_generation_maintenance ||
  failures=$((failures + 1))

feed_ready=true
if ! stage post-generation-embedding \
  compose --profile manual-embedding run --rm --no-deps \
    --name local-knowledge-portal-nightly-embedding-refresh \
    embedding-reindex python -m lkp_indexer.nightly_embedding_refresh
then
  failures=$((failures + 1))
  feed_ready=false
fi

if [ "$feed_ready" = true ]; then
  stage information-feed \
    compose --profile manual-developer-feed run --rm --no-deps \
      --name local-knowledge-portal-nightly-feed \
      -e LKP_DEVELOPER_FEED_DAILY_HOUR=0 \
      -e LKP_DEVELOPER_FEED_DAILY_SUMMARY_LAG_DAYS=1 \
      developer-feed ||
    failures=$((failures + 1))
else
  printf '{"event":"stage_skipped","stage":"information-feed","reason":"embedding_not_ready"}\n'
fi

if [ "$failures" -ne 0 ]; then
  printf '{"event":"nightly_completed","state":"completed_with_errors","failed_stages":%s}\n' \
    "$failures"
  exit 1
fi
printf '{"event":"nightly_completed","state":"succeeded","failed_stages":0}\n'
