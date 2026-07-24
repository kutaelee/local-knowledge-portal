# Operations runbook

## Start

1. Keep the Linux-native checkout at `~/src/local-knowledge-portal`.
2. Run `scripts/bootstrap-wsl-docker.ps1` from Windows once; review
   `C:\Docker\local-knowledge-portal\config`.
3. Run `./scripts/docker-stack.sh up` inside WSL.
4. Check `/health/live` and `/health/ready`. The stack starts PostgreSQL, migration, Ollama model
   pull, API, web, worker, watcher, reconciler, and hook collector in dependency order.

## Add a source root

Add an explicit existing directory to `config/source-roots.yaml`, keep `read_only: true`, and review excludes. Never register an entire drive. Run `scripts/scan.ps1`; inspect queue counts before starting many workers.

## Codex activity capture

Run `scripts/install-codex-hook.ps1` to idempotently merge the six global activity hooks into
`%USERPROFILE%\.codex\hooks.json`. Existing Codex configuration is copied to a timestamped
directory under `D:\LocalBackup\LocalKnowledgePortal\config\codex` before the merge.

Each hook only calls `scripts/codex-hook.ps1`, which redacts and atomically spools a bounded JSON
envelope to `E:\Data\LocalKnowledgePortal\ingest\codex-spool\pending`. It does not depend on the portal,
API, or database. `scripts/start-hook-collector.ps1` imports raw envelopes idempotently and keeps
reported results separate from exit-code-backed verified results. Malformed, unsupported, and
oversized envelopes are quarantined; a fallback spool is replayed after recovery.

Only meaningful instructions, changed files, failures, verification/operation commands, and
reported outcomes become activity history. Session lifecycle, acknowledgements, and read-only
inspection events do not become durable rows. A successfully handled raw envelope is deleted; only
failed or unclaimed envelopes remain in the spool for retry. Ordinary retained activity is still
not promoted to a wiki page. Create a knowledge
candidate explicitly and publish only after its category-specific evidence gate passes. Exact
problem/root-cause/resolution duplicates add occurrences and revisions to the existing canonical
case; uncertain similarity remains `NEEDS_REVIEW`.

### Canonical case search projection

Publishing a verified candidate writes a deterministic UTF-8 page under
`_generated/Knowledge-Cases`, records it in `generated_page`, and enqueues a priority indexing
job. The PostgreSQL case and append-only revisions are canonical; the Markdown and vectors are
rebuildable projections.

If guidance changes, create a new evidence-gated candidate with
`metadata.supersedes_case_id=<existing case UUID>`. Publication appends a revision and occurrence
before updating the current page. Never edit a generated page to change the canonical case.

Verify the projection with:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/cases
docker compose -f C:\Docker\local-knowledge-portal\compose.yaml exec -T postgres `
  psql -U lkp -d lkp -c "select relative_path, pipeline_version from generated_page"
```

Legacy `_generated/Runbooks` pages are ignored, not deleted. If a canonical page is missing, call
`POST /api/v1/knowledge/cases/{id}/materialize`; the operation refuses cases without verified
evidence.

Start a new Codex session, run `/hooks`, inspect the six commands, and approve them. Codex owns
this trust boundary, so the portal records `MANUAL_APPROVAL_REQUIRED` until the operator acts.

### Enable a local generation model

Set `LKP_GENERATION_PROVIDER=ollama`, `LKP_GENERATION_MODEL`, and a known
`LKP_GENERATION_MODEL_DIGEST`, then restart the service using generation. Raw activity capture
remains available if generation fails.

The first successful call resolves the installed digest from `/api/tags` and records it in the
generated result metadata. Copy that digest into configuration before treating output as revision
stable. Generated summaries never satisfy an evidence gate without independent execution evidence.

## Queue recovery

An interrupted processing job becomes claimable after `lease_expires_at`. Failed jobs back off exponentially and become `dead_letter` after `max_attempts`. The UI retry action creates a new job whose `error_details.retry_of` points to the original.

One source file is one durable job. Do not combine unrelated files merely to reduce the visible
pending count: file-level jobs preserve idempotency and provenance. The portal distinguishes active
backlog from cumulative completed history and estimates drain time from the preceding three hours.
Ready and expired-lease partial indexes keep claims bounded as history grows.

Before embedding, the worker applies `purpose-aware-v2`. Generated tokenizer payloads are ignored.
Repository code and nested Git dependencies are lexical-only in the default `docs_only` mode.
Lockfiles, minified/generated files, documents above 128 chunks, and documents above 250,000
characters are also indexed lexically with an explicit skip reason. Change the mode or limits
through configuration only after retrieval evaluation; do not remove the guard to make an initial
scan appear faster.

## Watcher incident

Treat file events as hints. If watchfiles fails, stop only the watcher and run reconciliation/scan. Do not touch source files. After Windows suspend/resume, run reconciliation before trusting freshness.

The WSL2 Docker deployment deliberately uses per-root hybrid monitoring:

- WSL ext4 roots such as `/home/kutae/src` use native filesystem notifications.
- Windows bind-mounted roots such as `/data/vault` use bounded polling. Keep
  `LKP_WATCH_POLL_DELAY_MS` at or above 1,000 ms; the default is 2,000 ms.
- Reconciliation remains enabled even when native events appear healthy.

In Operations → Workers, inspect the stable `watcher-service` row. Normal state is `healthy`
with `cpu_alert=false`; its metadata shows `root_watch_modes` and `process_cpu_percent`.
After the startup grace period, three consecutive samples at or above the configured 50%
threshold set the row to `error` and emit `watcher_cpu_alert_changed`.

If the alert fires, inspect source-root inode growth, accidental force-polling settings,
poll delay, and high-churn generated directories. Do not globally force polling for a large
WSL root. Use `scripts/watchfiles-bind-probe.py` to verify event delivery on a test bind mount,
then confirm missed events are recovered by reconciliation.

## Embedding incident

If Ollama is unavailable, new embedding jobs fail and retry; keyword retrieval over already indexed content remains available. A dimension mismatch is a hard failure. Correct the configured model/dimension or create a new embedding revision and explicitly reindex.

### CPU and thermal guard

The WSL2 Compose deployment applies a hard one-CPU quota to Ollama, worker, and API, plus a
half-CPU quota to watcher, hook collector, and web. Do not remove these limits to accelerate an
initial scan. Semantic throughput is intentionally bounded
with one-chunk embedding batches, inter-batch and inter-job delays, and a 20-job burst cooldown.
Lexical-only jobs never call Ollama and use a separate 200-job burst with a short cooldown under
the same worker CPU limit.

One CPU is the current safe Ollama default, not a universal optimum. Startup prewarming and
`OLLAMA_KEEP_ALIVE=24h` avoid repeated cold model loads; the 512-entry, 24-hour revision-aware
query cache removes repeat inference. Inspect dashboard search p50/p95 and cache occupancy before
considering CPU. Do not evaluate 1.5 CPUs until a real temperature sensor, representative workload,
30-minute thermal soak, and rollback evidence are available. Never jump directly to two CPUs.

Inspect the effective cgroup limits and current load:

```powershell
docker inspect -f '{{.Name}} NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}' `
  local-knowledge-portal-ollama-1 local-knowledge-portal-worker-1
docker stats --no-stream local-knowledge-portal-ollama-1 `
  local-knowledge-portal-worker-1 local-knowledge-portal-watcher-1
```

If temperature or total CPU remains unsafe, stop only model ingestion:

```powershell
docker stop --timeout 20 local-knowledge-portal-worker-1 `
  local-knowledge-portal-ollama-1
```

API, PostgreSQL, web, watcher, lexical search, and raw Codex spooling remain available. To pause
before the next lease without stopping containers, atomically create
`E:\Data\LocalKnowledgePortal\runtime\embedding.pause`. The worker heartbeat changes to `paused`.
Delete only that exact operator-created file to resume.

The expected worker heartbeat metadata includes `resource_guard_enabled=true`, batch and cooldown
values, and `pause_requested`. During a long job, both `last_seen_at` and `lease_expires_at` must
continue advancing. If either stalls, stop the worker and inspect transaction locks before adding
another worker.

The Ollama container mount must resolve to
`E:\AI\Models\Ollama -> /root/.ollama`. PostgreSQL remains in the Docker Desktop named volume on
C:, application data/spool/vault remains on E:, and append-only backups remain on D:. Verify the
effective mount with `docker inspect`; do not infer model placement from the Docker VHDX location.

Detailed evidence:
`docs/evidence/ollama-embedding-cpu-remediation-2026-07-23.md`.

## Low-value derived-data cleanup

Stop the watcher and worker, create and restore-test an immutable logical backup, then inspect the
cleanup dry run:

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f infra/docker/compose.wsl.yaml exec -T api python -m lkp.cleanup_derived
```

Apply only after the dry-run paths are confirmed:

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f infra/docker/compose.wsl.yaml exec -T api python -m lkp.cleanup_derived \
  --apply --manifest /data/exports/cleanup-ignored-YYYY-MM-DDTHHMMSSZ.json
```

The command targets only rows already classified `ignored`, refuses to run while target jobs are
active, deletes embeddings/chunks/versions/documents in dependency order, preserves source files,
and detaches rather than deletes ingest-job history. Validation activity cleanup requires the
additional explicit `--include-validation-fixtures` flag and recognizes only allowlisted fixture
session IDs.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur.
Run `./scripts/docker-stack.sh stop`. This stops every application container without deleting the
Docker named volume, E: data directory, or raw spool.

## Windows logon startup

Register the per-user logon task from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "\\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\install-auto-start.ps1"
```

The task `\LocalKnowledgePortal\StartAtLogon` starts Docker Desktop when necessary, invokes
Compose from the Ubuntu WSL distribution so Linux and `/mnt/*` bind paths retain their intended
meaning, and waits for API readiness. It does not build images during logon. Structured results
are appended to `E:\Data\LocalKnowledgePortal\runtime\logs\startup.jsonl`.

Do not replace the WSL Compose invocation with Windows `docker compose` while the repository and
environment use Linux paths. Doing so can create syntactically valid containers with empty bind
mounts. Validate the task after changes:

```powershell
Start-ScheduledTask -TaskPath '\LocalKnowledgePortal\' -TaskName 'StartAtLogon'
Get-ScheduledTaskInfo -TaskPath '\LocalKnowledgePortal\' -TaskName 'StartAtLogon'
docker compose -f C:\Docker\local-knowledge-portal\compose.yaml ps
```

`LastTaskResult` must be `0`, API readiness must return HTTP 200, and watcher, worker, and hook
collector must remain running.

## Codex activity capture boundary

Raw global hooks only write bounded atomic envelopes to
`E:\Data\LocalKnowledgePortal\ingest\codex-spool`. The collector discards lifecycle events,
acknowledgements, screenshots, file reads, status checks, and other low-signal tool output after
claiming it. It promotes file mutations, failed commands, and explicit test/build/backup/restore
operations into activity history.

The collector reads only `C:\Users\kutae\.codex\sessions` through a read-only container mount to
resolve the tool call's recorded exit code and, when needed, the current turn's user instruction
from a bounded transcript tail. It does not mount the rest of `.codex`.

Activities are not documents and are never chunked or embedded. A reported assistant result
remains narrative, not evidence. Across all Codex sessions, a completed turn becomes an automatic
candidate only when the same turn contains both a successful meaningful file mutation and a
successful test/lint/validation/build command with observed exit codes. Incomplete or partial
reports stop at the review-candidate state. Only a candidate that passes the existing evidence
gate may be published and projected into `_generated/Knowledge-Cases`.

When auditing capture, compare these layers separately in the portal:

- **Activity**: selected global work history, including reported and verified result columns.
- **Candidate review**: evidence-backed but incomplete or ambiguous work.
- **Knowledge cases**: verified canonical records only.
- **Documents**: source files and managed case projections; activity rows do not inflate this
  count.

If a reported success has no observed command exit, leave it `UNVERIFIED`. Do not manually edit
the managed Markdown page to work around the gate; attach evidence to a new candidate or rerun the
validation.

## Logs

Runtime files belong under `E:\Data\LocalKnowledgePortal\runtime`; container logs are available
through `./scripts/docker-stack.sh logs [service]`. Structured application logs never include full
source content.
