# Watcher CPU remediation evidence — 2026-07-23

## Incident

- Symptom: `local-knowledge-portal-watcher-1` continuously consumed 70.82–100.73% CPU
  while the watcher recorded zero filesystem events and zero new jobs during the preceding
  15 minutes.
- Scope: `/home/kutae/src` contained 107,426 inodes; `/home/kutae/src/ai` accounted for
  107,145.
- Runtime cause: the container kernel reported
  `6.18.33.2-microsoft-standard-WSL2`; `watchfiles 1.1.1` therefore selected
  `auto_force_polling=True`. Its default polling repeatedly traversed the large WSL source root.
- Additional finding: native filesystem notifications work for WSL ext4 bind mounts, but a
  Windows `E:` bind-mounted Vault did not deliver a native event.

## Corrective design

- Select watch mode per source root:
  - `/home/kutae/src`: native event notifications;
  - `/data/vault`: bounded polling at 2,000 ms.
- Retain periodic reconciliation at 300 seconds as the missed-event recovery path.
- Reject polling intervals below 1,000 ms in application settings.
- Record process CPU, selected root modes, thresholds, consecutive high samples, and alert
  state in a stable `watcher-service` heartbeat.
- Prioritize live watcher jobs ahead of initial scan jobs and commit the worker `busy`
  heartbeat immediately after leasing so queue progress is observable.
- Mark the heartbeat `error` after three consecutive samples at or above 50% CPU following a
  60-second startup grace period.
- Emit structured JSON on mode selection and CPU alert transitions.
- Expose watcher mode and CPU through `/api/v1/workers` and the Operations UI.
- Use the repository's version-controlled Compose file as the operational source of truth.

## Verification

Commands were executed against Docker Desktop's WSL2 backend.

- `ruff check services scripts tests`: passed.
- Unit tests: 22 passed.
- Dedicated PostgreSQL/pgvector integration tests: 6 passed; the temporary database container
  and named volume were removed.
- Next.js production build and TypeScript check: passed during the Docker build.
- WSL ext4 bind probe: native `added` event observed, probe exited 0, temporary resources removed.
- Windows-host managed Vault test:
  - initial missed native event recovered by startup reconciliation;
  - modify produced `watch_index`;
  - rename produced delete/create events while the original index job was still pending;
  - delete produced `watch_delete`.
- Steady-state external CPU samples after startup grace:
  `0.71%, 0.21%, 0.18%, 0.34%, 0.18%, 0.15%`.
- Heartbeat result: `healthy`, `watch_mode=hybrid`, `process_cpu_percent=0.25`,
  `cpu_alert=false`, `cpu_consecutive_high=0`.
- API readiness: PostgreSQL 18.4, schema `0002_activity_knowledge`, Ollama ready.

## Before and after

- Before: 70.82–100.73% watcher CPU with no events.
- After: 0.15–0.71% externally sampled CPU; 0.25% self-reported CPU.
- Reduction from the lowest observed baseline to the highest steady-state sample:
  approximately 99.0%.

## Boundaries

- This is a short steady-state verification, not a long-duration endurance test.
- Historical stale heartbeat rows were preserved; the new `watcher-service` ID is stable for
  future restarts.
- Validation ingest jobs are path-identifiable and were not deleted from operational history.

## Knowledge promotion

- Evidence gate: `VERIFIED`
- Candidate: `c67e1f0a-58c6-42fc-ba89-3242220a1e9a`
- Canonical case: `0af5a147-dd2c-4718-80f0-4a9074c3c73a`
- Publish outcome: `CREATED_CANONICAL`
- Managed wiki:
  `/data/vault/_generated/Runbooks/WSL2-Docker-Watcher-CPU.md`
- Wiki ingest job: priority 20, attempt 1, `succeeded`
- Keyword search: 8 provenance-bearing chunks returned
- Hybrid search: 8 matching chunks in top 20 with both keyword/path and semantic reasons
