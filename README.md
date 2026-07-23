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
- Ollama 0.32.1 for production semantic ingestion/search

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

## Global Codex activity capture

The user-level `%USERPROFILE%\.codex\hooks.json` records activity from every trusted Codex
workspace, not only this repository. `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`,
`SubagentStart`, and `SubagentStop` invoke a small PowerShell wrapper that only writes an atomic
JSON envelope to `E:\LocalKnowledgePortal\ingest\codex-spool\pending`. It never calls the API or
database. A bounded fallback spool, deterministic event IDs, secret redaction, malformed and
oversized quarantine, and collector-side idempotency keep capture available during portal or
database outages.

Ordinary Codex work is activity history only. It does not create or overwrite wiki pages.
Knowledge cases are created only through the evidence-gated candidate workflow. The installer
backs up the existing Codex configuration, merges only this portal's managed hooks, and is safe to
run repeatedly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-codex-hook.ps1
```

Codex requires a human trust review for non-managed hooks. Start a new session, run `/hooks`, and
approve the displayed commands. Until then the installation status is
`MANUAL_APPROVAL_REQUIRED`; no script attempts to bypass this boundary.

### Optional local LLM generation

Ingestion, activity capture, evidence gates, and keyword search do not require an LLM. Optional
generation is isolated behind a provider interface and disabled by default:

```dotenv
LKP_GENERATION_PROVIDER=ollama
LKP_GENERATION_BASE_URL=http://127.0.0.1:11434
LKP_GENERATION_MODEL=your-local-chat-model:tag
LKP_GENERATION_MODEL_DIGEST=unresolved
```

The adapter uses Ollama `/api/chat` structured outputs with temperature zero and fails closed on a
digest change. Other local runtimes can be added behind `GenerationProvider` without changing the
scanner, queue, evidence, or retrieval data models. Generated text is never accepted as verified
evidence by itself.

Production embedding uses Ollama `qwen3-embedding:0.6b`, digest
`ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`, dimension
1024, and revision `ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`. A dimension or
digest mismatch fails closed. Model changes require a new revision; vectors are never silently
mixed. Tests use a separate deterministic revision.

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

Integration and retrieval tests refuse to target the production database. Unit/integration tests
use a deterministic revision; retrieval evaluation uses the real configured Ollama model against
a dedicated bilingual/code corpus.

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
