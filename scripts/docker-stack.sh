#!/usr/bin/env bash
set -euo pipefail

compose_file=/mnt/c/Docker/local-knowledge-portal/compose.yaml
env_file=/mnt/c/Docker/local-knowledge-portal/.env

if [[ ! -f "$compose_file" || ! -f "$env_file" ]]; then
  echo "Run scripts/bootstrap-wsl-docker.ps1 from Windows first." >&2
  exit 1
fi

case "${1:-status}" in
  up)
    docker compose --env-file "$env_file" -f "$compose_file" up -d --build
    ;;
  down)
    docker compose --env-file "$env_file" -f "$compose_file" down
    ;;
  stop)
    docker compose --env-file "$env_file" -f "$compose_file" stop
    ;;
  restart)
    docker compose --env-file "$env_file" -f "$compose_file" restart
    ;;
  status)
    docker compose --env-file "$env_file" -f "$compose_file" ps -a
    ;;
  logs)
    shift
    docker compose --env-file "$env_file" -f "$compose_file" logs --no-color --tail=200 "$@"
    ;;
  backup)
    exec "$(dirname "$0")/backup-wsl-docker.sh"
    ;;
  *)
    echo "Usage: $0 {up|down|stop|restart|status|logs [service]|backup}" >&2
    exit 2
    ;;
esac
