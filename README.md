# Local Knowledge Portal

A standalone, localhost-only knowledge portal for read-only local repositories and Markdown vaults. The filesystem remains the source of truth; PostgreSQL 18.4 + pgvector 0.8.2 stores append-only versions, durable jobs, chunks, embeddings, retrieval indexes, and operational history.

## Safety model

- Source roots are read-only and allowlisted in `config/source-roots.yaml`.
- Scanner traversal rejects path escapes and does not follow symlinks or Windows reparse points.
- Binary and oversized files are skipped; generated wiki content is restricted to `_generated`.
- The API has no source mutation endpoint and rejects non-local bind configuration.
- Database, vectors, and caches are derived data. Backups are immutable dated directories and are never mirrored or pruned.
- `C:\Dev\Repos\local-voice-agent` and all other existing repositories are outside this repository's write boundary.

## Layout

| Purpose | Workstation default |
|---|---|
| Git and source | `C:\Dev\Repos\local-knowledge-portal` |
| PostgreSQL/models/cache/ingest/logs/vault/exports | `E:\LocalKnowledgePortal` |
| Immutable backups | `D:\Backups\LocalKnowledgePortal` |

All paths are configuration values. Committed files contain examples only.

## Prerequisites

- Windows 10/11 with PowerShell 5.1+
- Docker Desktop
- Python 3.13.14 managed by `uv` 0.11.30
- Node.js 24 LTS and `pnpm` 11.9.0
- Optional Ollama for semantic ingestion/search

The repository pins Python packages in `uv.lock`, JavaScript packages in `pnpm-lock.yaml`, and PostgreSQL/pgvector by exact tag and OCI digest.

## Bootstrap and run

PowerShell script execution may require an explicit process-scoped bypass:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
uv run uvicorn lkp.main:app --host 127.0.0.1 --port 8010
pnpm --filter @lkp/web dev
```

Then open:

- Portal: <http://127.0.0.1:3010>
- API documentation: <http://127.0.0.1:8010/docs>
- Live health: <http://127.0.0.1:8010/health/live>
- Readiness: <http://127.0.0.1:8010/health/ready>

Review `config/source-roots.yaml` before the first scan. The example registers `C:\Dev\Repos` read-only and excludes this portal to prevent self-indexing.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\scan.ps1
uv run python -m lkp_indexer.cli work-once
```

## Codex session capture

Codex transcripts remain read-only under `%USERPROFILE%\.codex\sessions`. The capture process
extracts only displayed user and assistant messages, redacts common secret shapes, and writes
managed Markdown below `E:\LocalKnowledgePortal\vault\_generated\codex-sessions`. It excludes
system/developer instructions, internal reasoning, and tool inputs/outputs.

This is a user-global Codex integration, not a hook for only this repository. The user-level
`%USERPROFILE%\.codex\hooks.json` applies across trusted Codex projects, and the polling process
observes all new or changed transcripts under the configured Codex home. If a WSL Codex CLI uses
its own Linux `~/.codex`, expose that path to Windows and add it to
`LKP_CODEX_ADDITIONAL_HOMES` as a semicolon-separated root.

Backfill every existing session explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\codex-sync.ps1 `
  -ImportExisting -Index
```

Backfill is explicit so installing the portal never silently copies historical conversations.

Start live capture with lexical indexing. Semantic embeddings stay explicitly pending when Ollama
is unavailable:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-codex-capture.ps1 -Index
```

For turn-completion capture without polling, install the user-level Codex `Stop` hook. The
installer refuses to overwrite an existing hook file. A new Codex session must review and trust
non-managed hooks using `/hooks`, as required by Codex:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-codex-hook.ps1
```

### Optional local LLM enrichment

Raw capture and search never require an LLM. A separate provider adapter can create managed
sidecar summaries under `_generated\codex-summaries`; it never rewrites the captured transcript
page. Ollama is implemented and disabled by default:

```dotenv
LKP_GENERATION_PROVIDER=ollama
LKP_GENERATION_BASE_URL=http://127.0.0.1:11434
LKP_GENERATION_MODEL=your-local-chat-model:tag
LKP_GENERATION_MODEL_DIGEST=unresolved
```

Restart capture after changing the provider. The adapter uses Ollama `/api/chat` structured
outputs with temperature zero, stores the actual model digest, and separates observed facts,
extracted information, and inferences requiring confirmation. After the first successful run,
pin the returned digest; a later digest change then fails closed. Other local runtimes can be
added behind the `GenerationProvider` interface without changing scanner, queue, or search models.

Production embedding defaults to Ollama model `qwen3-embedding:0.6b`, dimension 1024. A provider response with a different dimension fails closed. Model changes require a new `LKP_EMBEDDING_REVISION`; vectors are never silently mixed.

## Tests

```powershell
uv run ruff check services scripts tests
uv run pytest -m "not integration"
$env:LKP_TEST_DATABASE_URL = "postgresql+psycopg://.../dedicated_test_database"
uv run pytest -m integration
pnpm --filter @lkp/web build
$env:PLAYWRIGHT_BROWSERS_PATH = "E:\Cache\ms-playwright"
pnpm --filter @lkp/web test
```

The integration test uses a dedicated PostgreSQL database and a deterministic 1024-dimensional test embedder. It never pretends that Ollama was exercised.

## API surface

Implemented endpoints include keyword/hybrid/semantic search, RAG context, documents, versions, backlinks, projects, tree, jobs and auditable retry, workers, timeline, summary metrics, Prometheus text metrics, and split live/readiness health.

Every retrieval result includes document, version, chunk, source root, canonical/relative path, source lines, content hash, indexed time, retrieval score, and match reason.

## Operations

See:

- [Architecture](docs/architecture/system.md)
- [Operations runbook](docs/runbooks/operations.md)
- [Backup and restore](docs/runbooks/backup-restore.md)
- [Retrieval baseline](docs/evidence/retrieval-evaluation.md)
- [Validation evidence](docs/evidence/validation-report.md)
- [Known limitations](docs/known-limitations.md)
- [Backlog](docs/backlog.md)

## Rollback

Stop the API, web, and worker, then stop the Compose project. Source files require no rollback because ingestion never edits them. The derived PostgreSQL directory can be set aside and rebuilt from source roots; do not delete it automatically. Restore uses a verified custom-format dump into a new database first.
