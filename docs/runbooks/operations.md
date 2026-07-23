# Operations runbook

## Start

1. Run `scripts/bootstrap.ps1` once and review the two gitignored YAML files.
2. Run `scripts/start.ps1` to start PostgreSQL, migrate, and start the hook collector, watcher,
   reconciler, and worker.
3. Start API and web with the commands in the README.
4. Check `/health/live`; then check `/health/ready`. Readiness requires PostgreSQL, the current
   schema revision, and the configured Ollama embedding model.

## Add a source root

Add an explicit existing directory to `config/source-roots.yaml`, keep `read_only: true`, and review excludes. Never register an entire drive. Run `scripts/scan.ps1`; inspect queue counts before starting many workers.

## Codex activity capture

Run `scripts/install-codex-hook.ps1` to idempotently merge the six global activity hooks into
`%USERPROFILE%\.codex\hooks.json`. Existing Codex configuration is copied to a timestamped
directory under `D:\Backups\LocalKnowledgePortal\config\codex` before the merge.

Each hook only calls `scripts/codex-hook.ps1`, which redacts and atomically spools a bounded JSON
envelope to `E:\LocalKnowledgePortal\ingest\codex-spool\pending`. It does not depend on the portal,
API, or database. `scripts/start-hook-collector.ps1` imports raw envelopes idempotently and keeps
reported results separate from exit-code-backed verified results. Malformed, unsupported, and
oversized envelopes are quarantined; a fallback spool is replayed after recovery.

Ordinary work remains activity history and is not promoted to a wiki page. Create a knowledge
candidate explicitly and publish only after its category-specific evidence gate passes. Exact
problem/root-cause/resolution duplicates add occurrences and revisions to the existing canonical
case; uncertain similarity remains `NEEDS_REVIEW`.

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

## Watcher incident

Treat file events as hints. If watchfiles fails, stop only the watcher and run reconciliation/scan. Do not touch source files. After Windows suspend/resume, run reconciliation before trusting freshness.

## Embedding incident

If Ollama is unavailable, new embedding jobs fail and retry; keyword retrieval over already indexed content remains available. A dimension mismatch is a hard failure. Correct the configured model/dimension or create a new embedding revision and explicitly reindex.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur.
Stop API/web, then run `scripts/stop.ps1`. This stops the hook collector, watcher, worker, and
containers without deleting the E: data directory or raw spool.

## Logs

Runtime logs belong under `E:\LocalKnowledgePortal\runtime\logs`. JSON application logs include trace, job, worker, document, path, duration, and error fields without full source content.
