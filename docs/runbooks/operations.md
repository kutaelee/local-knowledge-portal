# Operations runbook

## Start

1. Run `scripts/bootstrap.ps1` once and review the two gitignored YAML files.
2. Run `scripts/start.ps1` to start PostgreSQL, migrate, and start Codex capture.
3. Start API and web with the commands in the README.
4. Check `/health/live`; then check `/health/ready`. Ollama may be false while keyword search remains available.

## Add a source root

Add an explicit existing directory to `config/source-roots.yaml`, keep `read_only: true`, and review excludes. Never register an entire drive. Run `scripts/scan.ps1`; inspect queue counts before starting many workers.

## Codex capture

`scripts/start-codex-capture.ps1 -Index` watches `%USERPROFILE%\.codex\sessions` read-only. On
first start it captures only the most recently active transcript, then captures new or changed
sessions. Output is restricted to `vault\_generated\codex-sessions`; a page without the expected
managed frontmatter is never overwritten.

The capture retains displayed user and assistant messages. It excludes system/developer messages,
internal reasoning, and tool I/O, and applies bounded secret-pattern redaction. When Ollama is
unavailable it creates lexical chunks with `embedding_status: pending`.

`scripts/install-codex-hook.ps1` installs the same capture on the Codex `Stop` event only when no
user `hooks.json` exists. Start a new Codex session, run `/hooks`, inspect the command, and trust it.
The polling process remains the fallback until that review is complete.

## Queue recovery

An interrupted processing job becomes claimable after `lease_expires_at`. Failed jobs back off exponentially and become `dead_letter` after `max_attempts`. The UI retry action creates a new job whose `error_details.retry_of` points to the original.

## Watcher incident

Treat file events as hints. If watchfiles fails, stop only the watcher and run reconciliation/scan. Do not touch source files. After Windows suspend/resume, run reconciliation before trusting freshness.

## Embedding incident

If Ollama is unavailable, new embedding jobs fail and retry; keyword retrieval over already indexed content remains available. A dimension mismatch is a hard failure. Correct the configured model/dimension or create a new embedding revision and explicitly reindex.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur.
Stop API/web, then run `scripts/stop.ps1`. This stops Codex capture and containers without deleting
the E: data directory.

## Logs

Runtime logs belong under `E:\LocalKnowledgePortal\runtime\logs`. JSON application logs include trace, job, worker, document, path, duration, and error fields without full source content.
