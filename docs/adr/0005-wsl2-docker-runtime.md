# ADR 0005: WSL2 and Docker-first runtime

Status: accepted, supersedes the active-runtime portions of ADR 0004.

## Decision

The canonical repository is Linux-native at `/home/kutae/src/local-knowledge-portal` inside the
Ubuntu WSL2 filesystem. Application services run through Docker Desktop. The active Compose
definition and secret environment file live at `C:\Docker\local-knowledge-portal`.

Docker Desktop itself is stored below `C:\Docker`; PostgreSQL uses a Docker named volume. Large
application files, raw activity spool, runtime files, vault, and exports use
`E:\Data\LocalKnowledgePortal`. Ollama model files use `E:\AI\Models\Ollama`. Immutable backups use
`D:\LocalBackup\LocalKnowledgePortal`.

The prior Windows-native checkout and `E:\LocalKnowledgePortal` data are retained as inactive
rollback material. They are not moved or deleted.

## Consequences

- Source operations avoid `/mnt/c` and `/mnt/e` performance and metadata limitations.
- PostgreSQL, Ollama, API, web, worker, watcher, reconciliation, and hook collection share one
  private Docker network; only API and web are published to Windows loopback.
- Windows Codex hooks remain available while WSL or the portal is down because the installed
  standalone PowerShell hook writes directly to the E: raw spool.
- New repositories intended for Linux/container operation belong below `~/src` and receive their
  active Compose definitions below `C:\Docker`.
