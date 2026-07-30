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
freshness, queue age and state, worker state, the active embedding/pipeline revision, query cache
occupancy, and last-hour search p50/p95. Throughput
charts use measured database events; they do not display placeholder series. API and RAG clients
continue to use the same provenance-bearing endpoints independently of the display language.

All portal screens, evidence labels, operations tables, empty/error states, document versions, and
search controls follow the selected Korean/English locale. Native select controls inherit the
active light/dark color scheme.

Readable input is not automatically embedded knowledge. A deterministic policy removes lifecycle
and read-only Codex noise before activity storage, ignores generated tokenizer payloads, and keeps
repository code, nested Git dependencies, lockfiles, and over-budget documents lexical-only by
default. Explorer projects are the top-level Git repositories below the source root, not an
incidental parent such as `ai`. Canonical cases require verified execution evidence, reusable
knowledge structure, and a qualified local evidence editor. See
[ADR 0006](docs/adr/0006-knowledge-value-selection.md) and
[ADR 0013](docs/adr/0013-local-llm-evidence-editor.md). File Explorer remains the
read-only source catalog, while Knowledge Cases are a separate verified layer. Cases and generated
project overviews are indexed by project, category, situation, lifecycle, and knowledge-value tags;
see [ADR 0015](docs/adr/0015-project-wiki-taxonomy-and-refresh.md).

The portal information architecture keeps these datasets explicit:

- **Source files**: registered repository files and human-authored Markdown, read-only. Managed
  `_generated` pages are excluded from this tree.
- **Unified search**: lexical, path, symbol, semantic, and hybrid retrieval across indexed source
  and managed knowledge, always with provenance.
- **Codex work**: selected raw activity and execution evidence; it is not canonical knowledge.
- **Project knowledge**: project journals plus verified reusable cases. Cases are browsed as
  project → work type, sorted by `last_seen_at DESC`, with localized display labels over stable
  canonical tags.
- **Ingest & operations / Change history / Document relations**: queue health, observed ingest
  events, and extracted document links respectively.

## Global Codex activity capture

The user-level `%USERPROFILE%\.codex\hooks.json` records activity from every trusted Codex
workspace, not only this repository. `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`,
`SubagentStart`, and `SubagentStop` invoke a small PowerShell wrapper that only writes an atomic
JSON envelope to `E:\Data\LocalKnowledgePortal\ingest\codex-spool\pending`. It never calls the API or
database. A bounded fallback spool, deterministic event IDs, secret redaction, malformed and
oversized quarantine, and collector-side idempotency keep capture available during portal or
database outages.

The PowerShell hook forces UTF-8 for redirected stdin and stdout. For Stop events the collector
prefers the matching UTF-8 Codex transcript result over the console payload; this prevents Windows
code-page mojibake from entering activity, journal, candidate, and generated Markdown data.

Ordinary Codex work is activity history only. It does not create or overwrite wiki pages. The
embedding model only creates retrieval vectors. A separate local evidence editor may publish a
canonical case only after model qualification, verified evidence, citation/number checks, and the
quality gate all pass. A Codex success report and model output are never evidence by themselves.
Managed case text follows `LKP_KNOWLEDGE_CONTENT_LANGUAGE=ko`, while paths, commands, variables,
and model identifiers remain unchanged.

Significant verified implementation, configuration, migration, runbook, and operating changes use
a separate project-development journal. They do not need to be reusable enough for a canonical
case. Each entry records the user intent, completion report, changed files, observed failed and
successful command families, and structured RAG provenance when the task actually used the
knowledge base. Single presentation-only changes remain activity history. The database retains the
full paginated journal. Each durable entry is written once below
`_generated/Projects/<project>/Journal/`, while the small
`_generated/Projects/<project>/development-journal.md` file is only a current index. This prevents
one new entry from re-embedding the project's entire history.

For a Windows repository collection, mount `C:\Dev\Repos` read-only at
`/sources/windows-repositories` and use `type: repository_collection`. Reconciliation scans only
direct child directories that are Git repositories; unrelated directories and nested dependency
repositories are not roots. `watch_mode: disabled` plus a bounded interval discovers future
repositories without one polling watcher per repository. The example remains disabled until it is
copied into the active source-root configuration intentionally.

Low-signal hook envelopes are discarded after collection. Successful tool detail that is not
linked to evidence is logically rolled up after 30 days and hidden from the default activity list;
user instructions, turn outcomes, failures, and evidence remain visible. The hourly retention
check never deletes database history. The installer backs up the existing Codex configuration,
merges only this portal's managed hooks, and is safe to run repeatedly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-codex-hook.ps1
```

Codex requires a human trust review for non-managed hooks. Start a new session, run `/hooks`, and
approve the displayed commands. Until then the installation status is
`MANUAL_APPROVAL_REQUIRED`; no script attempts to bypass this boundary.

Local-model conversations use a different intake path from Codex. A client that has completed an
Ollama turn calls `POST /api/v1/local-llm/hooks/chat`, or invokes
`scripts/capture-local-llm-chat.ps1`. The API only redacts and atomically writes the bounded
envelope to `/data/ingest/local-llm-spool`; the collector later imports it idempotently with
`activity_source=local_llm_chat`. Ollama itself has no global post-response hook, so a chat client
must configure this callback/adapter. These turns appear under **Local model chats**, carry an
explicit project key, remain separate from Codex activity, and are never treated as verified
execution evidence merely because a model said something.

### Local evidence editor

Ingestion, activity capture, evidence gates, and keyword search do not require an LLM. Long-form
case editing is isolated behind a provider interface:

```dotenv
LKP_GENERATION_PROVIDER=ollama
LKP_GENERATION_BASE_URL=http://host.docker.internal:11434
LKP_GENERATION_MODEL=qwen3.5:9b-q4_K_M
LKP_GENERATION_MODEL_DIGEST=6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7
LKP_KNOWLEDGE_CURATION_ENABLED=true
```

Hermes, portal embedding, and the evidence editor use the single Windows Ollama daemon at
`127.0.0.1:11434` and the single active model store
`E:\AI\Models\Ollama\generation\models`. Portal containers reach that loopback-only daemon through
Docker Desktop's `host.docker.internal` gateway. Legacy Compose Ollama services are kept behind the
`legacy-ollama` profile for rollback only and must not run concurrently with the workstation
daemon.
`qwen3.5:9b-q4_K_M` is the qualified production editor because the available smaller model did not
reliably satisfy the structured evidence contract. The model only edits verified inputs into
readable Korean prose; deterministic code owns publication state, deduplication, revisions, tags,
citations, and generated page updates. There is no minimum character quota. Optional context is
omitted instead of padded, while unsupported claims are excluded and retained for review.

Every curation run that reserves at least 2 GiB or is expected to exceed 30 seconds must enter the
workstation GPU scheduler:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\curate.ps1
```

The script submits a one-shot Compose curator through `gpuq run`, deduplicates an already active or
queued portal curation workload, and never exposes the scheduler mutation token to the portal.
Each run freezes the eligible candidate IDs at its start and attempts that complete snapshot.
Candidates created or made eligible while the run is active are deferred to the next hourly run.
One candidate failure is recorded without preventing the remaining snapshot from being attempted.
`/gpu-queue` is a Korean/English view of GPU capacity, observed external use,
active/queued/completed jobs, effective priority, and scheduling notes. It can
request a safe stop only for a scheduler-managed child process and can persist a
complete drag-and-drop queued order. The browser never receives the host control
token; the API uses a server-side token configured with
`scripts/configure-gpu-queue-control.ps1`. See
[ADR 0013](docs/adr/0013-local-llm-evidence-editor.md).

Install the daily, non-overlapping maintenance task after the GPU scheduler is available:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\install-nightly-maintenance-schedule.ps1
```

At 00:30 `Asia/Seoul`, one GPUQ reservation waits behind earlier work and then runs semantic
maintenance, curation/project articles, and the previous day's information feed sequentially.
The installer disables the superseded repeating model schedules. Every stage uses a bounded
one-shot container and unloads its exact model before the reservation exits.

Production embedding uses Ollama `qwen3-embedding:0.6b`, digest
`ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`, dimension
1024, and revision `ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`. A dimension or
digest mismatch fails closed. Model changes require a new revision; vectors are never silently
mixed. Tests use a separate deterministic revision.

The WSL2 runtime keeps ingestion thermally bounded: worker and API have one CPU; watcher, hook
collector, and web each have half a CPU. Embedding requests use bounded small batches and the
runtime may be set to `deferred_gpu_recovery` while another GPU reservation is active. Query
embeddings use a bounded revision-aware cache and search SQL has a bounded execution time. Multi-term keyword
fallback stays on the GIN-backed text vector and requires at least two matching terms; expensive
trigram fuzzy matching is bounded to short single-term typo recovery. Semantic and lexical
jobs have separate cooldown/burst policies, so an initial code catalog scan drains without calling
the model while semantic work remains conservative. Operators can create
`E:\Data\LocalKnowledgePortal\runtime\embedding.pause` to stop new leases while keeping the portal
and lexical search online. See the operations runbook before changing these defaults.
The evidence required before evaluating a larger CPU profile is documented in
[ADR 0012](docs/adr/0012-mount-and-thermal-fail-closed.md).

## Tests

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /mnt/c/Docker/local-knowledge-portal/compose.yaml run --rm api \
  ruff check services scripts tests
```

Integration and retrieval tests refuse to target the production database. Unit/integration tests
use a deterministic revision; retrieval evaluation uses the real configured Ollama model against
a dedicated bilingual/code corpus.

Run the integration suite from WSL with a fresh private-network database:

```bash
sh scripts/run-isolated-integration-tests.sh
```

The runner generates a new `lkp_test_verify_*` database, refuses to reuse one,
mounts the repository read-only into a one-off test container, and drops only
the database it created in its exit trap. It never connects the tests to the
production database.

## API surface

Implemented endpoints include keyword/hybrid/semantic search, RAG context, documents, versions,
backlinks, projects, tree, jobs and auditable retry, workers, timeline, summary metrics, Prometheus
text metrics, split live/readiness health, indexed knowledge facets, source/managed catalog
boundaries, `/api/v1/system/services`, and `/api/v1/embedding/recovery`. The recovery endpoint
reports only the portal-owned semantic-recovery reservation and scheduler decision; it exposes no
scheduler mutation path or command arguments. The service catalog checks the persistent WSL/Docker services
without exposing the Docker socket. A bounded Windows collector runs every 30 seconds and
atomically writes `E:\Data\LocalKnowledgePortal\runtime\docker-services.json`; Compose projects
added later appear automatically. The UI groups results as Local Knowledge Portal, project
web/API stacks, and shared infrastructure. Install or repair the collector with
`scripts/install-docker-health-monitor.ps1`.

Project knowledge uses the same `project → work type → newest record` tree for verified cases,
held candidates, and project journal entries. Filters are applied server-side so pagination totals
remain accurate.

The GPU scheduler integration exposes bounded health/status/job GETs plus a
server-side safe-stop request and complete queued-order update. The upstream
host URL is restricted to loopback or `host.docker.internal`. The browser
cannot call the host scheduler directly and never receives its token.

`/api/v1/knowledge/dedup/status` reports pending non-exact candidates and rebuildable dedup-vector
coverage. Production duplicate checking and semantic verification share the first stage of
`\LocalKnowledgePortal\NightlyKnowledgeMaintenance`; the former repeating dedup task remains
disabled for rollback.

Every retrieval result includes document, version, chunk, source root, canonical/relative path, source lines, content hash, indexed time, retrieval score, and match reason.

## Operations

Always start this stack through WSL:

```bash
./scripts/docker-stack.sh up
```

Do not run the Linux-path Compose file with the Windows Docker CLI. A `/mnt/e` bind can otherwise
land on a small Docker Desktop internal disk instead of `E:\`. The runbook includes the mount
capacity check.

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
