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
Run `./scripts/docker-stack.sh stop`. This stops every application container without deleting the
Docker named volume, E: data directory, or raw spool.

## Logs

Runtime files belong under `E:\Data\LocalKnowledgePortal\runtime`; container logs are available
through `./scripts/docker-stack.sh logs [service]`. Structured application logs never include full
source content.
