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

The WSL2 Compose deployment applies a hard two-CPU quota to Ollama and a one-CPU quota to the
worker. Do not remove these limits to accelerate an initial scan. Throughput is intentionally
bounded with two-chunk embedding batches, inter-batch and inter-job delays, and a 20-job burst
cooldown.

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

Detailed evidence:
`docs/evidence/ollama-embedding-cpu-remediation-2026-07-23.md`.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur.
Run `./scripts/docker-stack.sh stop`. This stops every application container without deleting the
Docker named volume, E: data directory, or raw spool.

## Logs

Runtime files belong under `E:\Data\LocalKnowledgePortal\runtime`; container logs are available
through `./scripts/docker-stack.sh logs [service]`. Structured application logs never include full
source content.
