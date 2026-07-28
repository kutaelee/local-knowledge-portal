#!/usr/bin/env sh
# Run only as the argv workload of an admitted gpuq reservation.
set -eu

repo_dir="/home/kutae/src/local-knowledge-portal"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"
compose_file="$repo_dir/infra/docker/compose.wsl.yaml"
package="${1:-/data/repository-analysis/results/indigoesb-support-retrieval.json}"

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

# Create missing vectors first, then use the exact active embedding revision to
# prepare latest-snapshot hybrid retrieval contexts. The package contains
# validated knowledge only, never repository source text.
compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.repository_embedding_reindex
compose --profile manual-embedding run --rm --no-deps embedding-reindex \
  python -m lkp_indexer.repository_retrieval_evaluation prepare \
  --output "$package" \
  --limit 5
