# Local Knowledge Portal

A standalone, localhost-only knowledge portal for read-only local repositories and Markdown vaults. The filesystem remains the source of truth; PostgreSQL 18.4 + pgvector 0.8.2 stores append-only versions, durable jobs, chunks, embeddings, retrieval indexes, and operational history.

## Safety model

- Source roots are read-only and allowlisted in `config/source-roots.yaml`.
- Scanner traversal rejects path escapes and does not follow symlinks or reparse points.
- Binary and oversized files are skipped; generated wiki content is restricted to `_generated`.
- The API has no source mutation endpoint and rejects non-local bind configuration.
- Database, vectors, and caches are derived data. Backups are immutable dated directories and are never mirrored or pruned.
- Other repositories below `~/src` are read-only source roots and remain outside this
  repository's write boundary.

## Layout

| Purpose | Workstation default |
|---|---|
| Git and source | WSL2 `/home/kutae/src/local-knowledge-portal` |
| Active Compose definition and Docker volumes | `C:\Docker\local-knowledge-portal`, Docker Desktop under `C:\Docker` |
| Ingest/logs/vault/exports | `E:\Data\LocalKnowledgePortal` |
| Ollama model store | `E:\AI\Models\Ollama` |
| Immutable backups | `D:\LocalBackup\LocalKnowledgePortal` |

All paths are configuration values. Committed files contain examples only.

## Prerequisites

- Windows 10/11 with WSL2 Ubuntu and PowerShell 5.1+
- Docker Desktop with Ubuntu integration and data root under `C:\Docker`
- Git inside WSL2

Python, Node, PostgreSQL/pgvector, and Ollama run in pinned container images. Package lockfiles
remain authoritative inside those builds.

## Bootstrap and run

Clone the repository inside the WSL filesystem. Do not place the Linux checkout below `/mnt/c`
or `/mnt/e`.

```bash
mkdir -p ~/src
git clone https://github.com/kutaelee/local-knowledge-portal.git ~/src/local-knowledge-portal
```

From Windows, create the active Compose/config/data/backup layout and install the standalone
global Codex spool hook:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  \\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\bootstrap-wsl-docker.ps1
```

Then run inside WSL:

```bash
cd ~/src/local-knowledge-portal
./scripts/docker-stack.sh up
```

Portal, API docs, live health, and readiness remain available only on loopback at ports 3010 and
8010. PostgreSQL and Ollama are not published to the host network.

## Human-facing portal

The portal defaults to Korean and can be switched to English from the top bar. The preference is
saved in the browser and the document language is updated for assistive technology.

The overview is intended for human verification rather than model consumption. It shows the data
snapshot time, last indexed source time, most recently indexed documents, per-source reconciliation
freshness, queue age and state, worker state, and the active embedding/pipeline revision. Throughput
charts use measured database events; they do not display placeholder series. API and RAG clients
continue to use the same provenance-bearing endpoints independently of the display language.

All portal screens, evidence labels, operations tables, empty/error states, document versions, and
search controls follow the selected Korean/English locale. Native select controls inherit the
active light/dark color scheme.

Readable input is not automatically embedded knowledge. A deterministic policy removes lifecycle
and read-only Codex noise before activity storage, ignores generated tokenizer payloads, and keeps
repository code, nested Git dependencies, lockfiles, and over-budget documents lexical-only by
default. Explorer projects are the top-level Git repositories below the source root, not an
incidental parent such as `ai`. Canonical cases still require verified evidence. See
[ADR 0006](docs/adr/0006-knowledge-value-selection.md) and
[ADR 0007](docs/adr/0007-purpose-scoped-retrieval.md).

## Global Codex activity capture

The user-level `%USERPROFILE%\.codex\hooks.json` records activity from every trusted Codex
workspace, not only this repository. `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`,
`SubagentStart`, and `SubagentStop` invoke a small PowerShell wrapper that only writes an atomic
JSON envelope to `E:\Data\LocalKnowledgePortal\ingest\codex-spool\pending`. It never calls the API or
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

The optional adapter uses the private Docker service `ollama` and structured outputs with
temperature zero, then fails closed on a
digest change. Other local runtimes can be added behind `GenerationProvider` without changing the
scanner, queue, evidence, or retrieval data models. Generated text is never accepted as verified
evidence by itself.

Production embedding uses Ollama `qwen3-embedding:0.6b`, digest
`ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`, dimension
1024, and revision `ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`. A dimension or
digest mismatch fails closed. Model changes require a new revision; vectors are never silently
mixed. Tests use a separate deterministic revision.

The WSL2 runtime keeps embedding thermally bounded: Ollama and the worker each have a one-CPU
Docker quota, model concurrency is one, and requests use one-chunk batches. Semantic and lexical
jobs have separate cooldown/burst policies, so an initial code catalog scan drains without calling
the model while semantic work remains conservative. Operators can create
`E:\Data\LocalKnowledgePortal\runtime\embedding.pause` to stop new leases while keeping the portal
and lexical search online. See the operations runbook before changing these defaults.

## Tests

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /mnt/c/Docker/local-knowledge-portal/compose.yaml run --rm api \
  ruff check services scripts tests
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
- [WSL2 transition evidence](docs/evidence/wsl2-transition-report.md)
- [Ollama CPU remediation](docs/evidence/ollama-embedding-cpu-remediation-2026-07-23.md)
- [Known limitations](docs/known-limitations.md)
- [Backlog](docs/backlog.md)

## Rollback

Run `./scripts/docker-stack.sh stop`. Source files require no rollback because ingestion never
edits them. Keep the named PostgreSQL volume and legacy `E:\LocalKnowledgePortal` data intact;
never delete either automatically. Restore a custom-format dump into a new database before any
cutover.
