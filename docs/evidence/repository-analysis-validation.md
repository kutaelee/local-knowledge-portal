# Repository analysis implementation and validation report

Date: 2026-07-26
Scope: one additive `repository_analysis` module in Local Knowledge Portal

## Discovery

- The canonical repository is Linux-native at `/home/kutae/src/local-knowledge-portal`.
- PostgreSQL/pgvector, FastAPI, the existing Next.js portal, source allowlists, and the managed
  knowledge pipeline are reused.
- Source roots are read-only. Production PostgreSQL and existing knowledge were not changed.
- The worktree already contained unrelated user changes. This implementation used new module,
  migration, route, UI, test, and documentation files plus minimal router/sidebar/environment
  integration, without reverting existing edits.

## Implemented architecture

```text
allowlisted source
  -> deterministic fingerprint and Git metadata
  -> analyzer plugin registry
  -> file/symbol/relation/config/dependency facts
  -> optional local OpenAI-compatible model
  -> claims
  -> file/hash/line/symbol/config/dependency validation
  -> support knowledge metadata
  -> 30 evidence questions + 10 troubleshooting scenarios
  -> additive repository_* PostgreSQL schema
  -> grounded FastAPI query tools
  -> existing portal /repository-analysis console
```

The database schema has no source-content column. Configuration values, secrets, complete source
files, and decompiler output are not represented in the analysis manifest or persistence schema.

## Changed files

New:

- `services/indexer/lkp_indexer/repository_analysis/{__init__,__main__,domain,discovery,analyzers,provider,validator,pipeline,repository,evaluation}.py`
- `services/api/lkp/repository_analysis_routes.py`
- `db/migrations/versions/0015_repository_analysis.py`
- `apps/web/app/repository-analysis/page.tsx`
- `apps/web/components/repository-analysis.tsx`
- `tests/unit/test_repository_analysis.py`
- `tests/integration/test_repository_analysis.py`
- `docs/repository-analysis.md`
- `docs/evidence/repository-analysis-validation.md`

Integrated:

- `.env.example`
- `services/api/lkp/main.py`
- `apps/web/components/portal.tsx`
- `apps/web/app/globals.css`

## Database migration

`0015_repository_analysis` adds project, immutable snapshot, source fact, analysis job/task/claim,
knowledge item, and evaluation case/result tables. Snapshot deduplication is enforced by
`(project_id, source_hash)`. Previous snapshots are retained and marked stale when a new source
hash is persisted. Downgrade is intentionally non-destructive.

The migration was applied through Alembic in a newly created private test database. The database
was dropped by the runner's exit trap after the suite.

## Analyzer plugins

- Python: AST classes, functions, imports, calls, and inheritance.
- Java, JavaScript/TypeScript, Kotlin, shell, SQL, Gradle: bounded declarations and direct calls.
- XML: bean, route, endpoint, queue, and topic identifiers.
- YAML, JSON, TOML, properties: keys and declaration locations only.
- Maven, NPM, PyPI: declared dependency metadata.

Symlinks, binary files, common sensitive filenames, oversized files, unsupported files, and
non-UTF-8 files are skipped with observable reasons.

## Local model provider

The provider is disabled by default. It accepts only loopback, `host.docker.internal`, or the
internal `ollama` hostname; it has no cloud fallback. Requests are bounded, temperature zero, and
require a JSON object. Model, prompt, token, latency, context, output, timeout, concurrency, and
quantization settings are represented.

No model was invoked in this validation, so model answer quality is not claimed.

## API and web UI

Implemented project, snapshot, status, evaluation, search, and grounded repository knowledge tool
endpoints under `/api/v1/repository-analysis`. Search defaults to searchable knowledge on the
latest non-stale snapshot and always returns project, snapshot, validation, confidence, evidence,
and unknown fields.

The existing sidebar links to `레포 분석`. The console includes project search, latest Snapshot,
language/build/dirty state, file/symbol/relation/config metrics, ten detail tabs, job state, and an
explicit distinction between evidence-integrity evaluation and unexecuted answer-quality
evaluation.

Browser verification on a temporary loopback-only Next.js server confirmed:

- `/repository-analysis` renders the policy header and API-error state without layout breakage.
- the existing portal contains exactly one accessible `/repository-analysis` sidebar link.
- the temporary port 3011 listener was stopped after verification.

No production deployment was performed.

## Actual read-only smoke analysis

Target:
`services/indexer/lkp_indexer/repository_analysis`

Observed result:

- files: 10
- symbols: 82
- static relations: 617
- claims: 82
- source-verified claims: 82
- generated knowledge items: 1
- searchable knowledge items: 1
- evidence questions: 30
- troubleshooting scenarios: 10
- evidence-integrity results passed: 40/40
- warnings: 0
- source text stored: false
- total measured pipeline time: 25 ms

The 30 question cases rotate through structure, entry point, change impact, configuration,
exception, data flow, concurrency, dependency, log diagnosis, and counter-evidence checks for
actual discovered symbols. The ten scenario cases are:

1. connection timeout
2. retry exhausted
3. duplicate message
4. message loss
5. consumer missing
6. configuration override
7. dependency missing
8. class loading failure
9. transaction rollback
10. thread pool exhaustion

These 40 results validate expected file/hash/line/symbol integrity only. They do not represent
generated support answers, retrieval Recall@K/Precision@K/MRR, or human usability scores.

## Executed tests

- Ruff on the new backend, migration, routes, and tests: passed.
- Python unit suite: 133 passed.
- Isolated PostgreSQL integration suite: 31 passed.
- Next.js TypeScript check: passed.
- Next.js 16.2.11 production build: passed; `/repository-analysis` prerendered.
- Read-only module smoke analysis: completed with the metrics above.

The integration suite reports one upstream Starlette deprecation warning about TestClient's
httpx adapter. It is unrelated to this module.

One initial integration run exposed an ambiguous PostgreSQL type for optional `NULL` filters.
UUID and text filter parameters were explicitly cast; the repeated 31-test run passed.

## Security verification

- allowlist path escape rejection: tested.
- sensitive filename exclusion: tested.
- secret value absent from serialized manifest: tested.
- source-content columns absent from `repository_source_file`: tested against PostgreSQL.
- duplicate Snapshot persistence: tested.
- rejected external model hostname: tested.
- production database mutation: not performed.
- external network/model call: not performed.

## Known gaps and next work

- No production migration or production analysis was authorized or executed.
- No local-model answer generation or human answer-quality evaluation was executed.
- `repository_knowledge_item.document_id` is the quality-gated integration seam to existing
  document/chunk/embedding retrieval. Managed-document materialization and vector indexing were
  not executed, so vector Recall@K, Precision@K, and MRR are not claimed.
- Static plugins are conservative and do not fully resolve type systems, reflection, dynamic
  dispatch, runtime configuration precedence, transactions, or concurrency.
- Deep file/symbol/relation drill-down can be added after representative project data is approved
  for the non-production pipeline.

## Rollback

Disable the route/sidebar and keep `REPO_ANALYSIS_MODEL_ENABLED=false`. Do not automatically drop
the additive tables. Any schema removal requires a verified logical backup, restore into a new
test database, and an explicitly reviewed destructive migration. Analyzed source needs no rollback
because the module never writes it.
