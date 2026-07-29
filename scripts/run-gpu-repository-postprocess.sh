#!/usr/bin/env sh
# Run only as the argv workload of an admitted gpuq reservation.
set -eu

repo_dir="/home/kutae/src/local-knowledge-portal"
env_file="${LKP_ENV_FILE:-/mnt/c/Docker/local-knowledge-portal/.env}"
compose_file="$repo_dir/infra/docker/compose.wsl.yaml"
package="${1:-/data/repository-analysis/results/indigoesb-support-retrieval.json}"
evaluation_output="${LKP_REPOSITORY_EVALUATION_OUTPUT:-/mnt/e/Data/LocalKnowledgePortal/repository-analysis/results/backend-rag-evaluation-latest.json}"

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

# Keep the representative, boundary, verified-failure, and intentional
# no-answer evidence synchronized with the exact post-reindex state. This
# remains inside the admitted gpuq reservation because API query embeddings
# can briefly use the same Ollama model.
mkdir -p "$(dirname "$evaluation_output")"
PYTHONPATH="$repo_dir/services/api:$repo_dir/services/indexer" \
  uv run python "$repo_dir/scripts/evaluate_rag_quality.py" \
  --base-url http://127.0.0.1:8010 \
  --candidate-k 50 \
  --workers 6 \
  --output "$evaluation_output"
