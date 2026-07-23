# Local Knowledge Portal contributor guide

This repository is a standalone Windows-native monorepo.

- Source and Git history stay under `C:\Dev\Repos\local-knowledge-portal`.
- Runtime data is configurable; workstation defaults live under `E:\LocalKnowledgePortal`.
- Backups are append-only dated directories under `D:\Backups\LocalKnowledgePortal`.
- Source roots are read-only. Never move, rename, delete, or rewrite indexed source files.
- Generated wiki pages may only be written below a configured `managed_subdirectory`.
- Use `pnpm` for JavaScript and `uv` for Python. Commit both lockfiles.
- Never commit `.env`, `config/source-roots.yaml`, database dumps, source data, or credentials.
- Run `scripts/validate.ps1` before publishing.

See `README.md` and `docs/runbooks/operations.md` for build, test, runtime, data, and artifact paths.
