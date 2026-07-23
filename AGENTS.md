# Local Knowledge Portal contributor guide

This repository is a standalone Linux-native WSL2 monorepo operated through Docker Desktop.

- Source and Git history stay under `/home/kutae/src/local-knowledge-portal`, never `/mnt/c` or `/mnt/e`.
- Active Compose definitions, secrets, and Docker Desktop volumes belong under
  `C:\Docker\local-knowledge-portal`; only examples are committed.
- Large application data belongs under `E:\Data\LocalKnowledgePortal`; Ollama models belong under
  `E:\AI\Models\Ollama`.
- Backups are append-only dated directories under `D:\LocalBackup\LocalKnowledgePortal`.
- Application services run as containers. Do not start parallel Windows-native API, web, worker,
  watcher, PostgreSQL, or Ollama instances on the same ports.
- Source roots are read-only. Never move, rename, delete, or rewrite indexed source files.
- Generated wiki pages may only be written below a configured `managed_subdirectory`.
- Use `pnpm` for JavaScript and `uv` for Python. Commit both lockfiles.
- Never commit `.env`, `config/source-roots.yaml`, database dumps, source data, or credentials.
- Run tests in an ephemeral container or dedicated test database before publishing.

See `README.md` and `docs/runbooks/operations.md` for build, test, runtime, data, and artifact paths.
