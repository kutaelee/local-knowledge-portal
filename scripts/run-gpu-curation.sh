#!/usr/bin/env sh
# This wrapper is submitted by scripts/curate.ps1 to gpuq. The curator reaches
# the workstation-wide Windows Ollama through host.docker.internal. gpuq owns
# admission; this wrapper never starts a second Ollama daemon.
set -eu

compose() {
  docker compose \
    --env-file /mnt/c/Docker/local-knowledge-portal/.env \
    -f /home/kutae/src/local-knowledge-portal/infra/docker/compose.wsl.yaml \
    "$@"
}

if [ "${1:-}" = "--project" ]; then
  if [ "$#" -ne 2 ] || [ -z "$2" ]; then
    echo "usage: run-gpu-curation.sh --project <project-key>" >&2
    exit 2
  fi
  compose --profile manual-curation run --rm --no-deps \
    project-article-curator python -m lkp_indexer.project_article --project "$2"
  exit $?
fi

# The curator performs the bounded model/digest verification itself before
# inference and records the exact digest with the resulting revision.
compose --profile manual-curation run --rm --no-deps knowledge-curator

# Project articles share the same admitted model runtime. Each project is
# compared with its prior source manifest and only changed evidence is edited
# into a new append-only revision.
compose --profile manual-curation run --rm --no-deps project-article-curator
