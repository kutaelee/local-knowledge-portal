# Operations runbook

## Start

1. Run `scripts/bootstrap.ps1` once and review the two gitignored YAML files.
2. Run `scripts/start.ps1` to start PostgreSQL and migrate.
3. Start API and web with the commands in the README.
4. Check `/health/live`; then check `/health/ready`. Ollama may be false while keyword search remains available.

## Add a source root

Add an explicit existing directory to `config/source-roots.yaml`, keep `read_only: true`, and review excludes. Never register an entire drive. Run `scripts/scan.ps1`; inspect queue counts before starting many workers.

## Queue recovery

An interrupted processing job becomes claimable after `lease_expires_at`. Failed jobs back off exponentially and become `dead_letter` after `max_attempts`. The UI retry action creates a new job whose `error_details.retry_of` points to the original.

## Watcher incident

Treat file events as hints. If watchfiles fails, stop only the watcher and run reconciliation/scan. Do not touch source files. After Windows suspend/resume, run reconciliation before trusting freshness.

## Embedding incident

If Ollama is unavailable, new embedding jobs fail and retry; keyword retrieval over already indexed content remains available. A dimension mismatch is a hard failure. Correct the configured model/dimension or create a new embedding revision and explicitly reindex.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur. Stop API/web, then run `scripts/stop.ps1`. This stops containers without deleting the E: data directory.

## Logs

Runtime logs belong under `E:\LocalKnowledgePortal\runtime\logs`. JSON application logs include trace, job, worker, document, path, duration, and error fields without full source content.
