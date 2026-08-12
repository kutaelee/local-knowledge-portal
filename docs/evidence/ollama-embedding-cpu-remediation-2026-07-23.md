# Ollama embedding CPU remediation evidence — 2026-07-23

## Incident

- The user reported a CPU temperature of 92°C. Windows
  `MSAcpi_ThermalZoneTemperature` was unavailable, so that temperature is retained as a
  reported value rather than independently verified evidence.
- `docker stats` measured `local-knowledge-portal-ollama-1` at 1503.60% CPU. Windows
  `Get-Counter` measured total host CPU at 61.745%.
- The watcher was not the source of this incident: it measured 0.38% externally and its
  internal alert remained false.
- The queue contained 2,455 pending jobs, including 1,415 paths below
  `/home/kutae/src/ai/Block-Sparse-Attention`.

## Root cause

1. The Ollama container had no Docker CPU quota, so one embedding request could use roughly
   fifteen logical CPUs.
2. A document's missing chunks were sent to `/api/embed` as one unbounded request. There was no
   inter-batch, inter-job, or burst cooldown.
3. Initial scanning could create a long-lived backlog. Serial queue handling limited concurrency
   but did not limit sustained model duty cycle.
4. The processing transaction updated `ingest_job` and `worker_heartbeat` before a long embedding
   call. Those row locks blocked the lease-renewal transaction, creating a duplicate-processing
   risk when a request exceeded the lease interval.

## Corrective design

- Docker hard limits:
  - Ollama: 2 CPUs, 6 GiB, 256 PIDs;
  - worker: 1 CPU, 1 GiB, 256 PIDs.
- Ollama request controls: one parallel request, one loaded model, and queue capacity eight.
- Embedding batches are limited to two chunks with a 0.5-second cooling interval.
- The worker waits one second between jobs and cools for 15 seconds after each 20-job burst.
- `/data/runtime/embedding.pause` prevents new leases and exposes state `paused` in heartbeat
  metadata. Removing the operator-created pause file resumes processing.
- Resource-policy values and pause state are stored in worker heartbeat metadata and emitted as
  structured JSON.
- The worker commits `processing` before entering the document transaction. It does not modify
  the job or heartbeat row until completion, leaving lease renewal free to advance during long
  model calls.

## Verification

- `docker inspect`:
  - Ollama `NanoCpus=2000000000`, memory `6442450944`, PIDs `256`;
  - worker `NanoCpus=1000000000`, memory `1073741824`, PIDs `256`.
- Five post-remediation Ollama samples:
  `195.90%, 194.56%, 80.74%, 180.45%, 192.50%`.
- Corresponding host samples: `19.4%, 19.8%, 22.5%, 18.1%, 20.6%`.
- A later sample measured Ollama `201.85%`, host `21.0%`, watcher `0.22%`. The small
  sampling overshoot did not change the configured two-CPU cgroup quota.
- For live job `79079403-e0a7-402f-9329-6e6903cb34c0`, heartbeat advanced from
  `14:09:05.934989Z` to `14:09:25.955107Z`; lease expiry advanced from
  `14:11:05.923955Z` to `14:11:25.952726Z`.
- `ruff` passed and 24 unit tests passed.
- Six integration tests passed against a dedicated temporary PostgreSQL/pgvector database.
  The temporary container and named volume were removed.
- Next.js production build and TypeScript validation passed. The built web image manifest is
  `sha256:e6cea463290fa3fdec6386770e5c62fcd67f0bebffd80a7b01810b5882e1865a`.
- The managed wiki page was detected as priority 20 and succeeded on attempt one:
  - document `0c1bf7b2-94ae-4f7d-ac25-5c5bedfd3b01`;
  - version `46adc821-4fd9-4d73-9de8-b282fd8a5211`;
  - 9 chunks and 9 production embeddings.
- Hybrid retrieval returned the wiki page with keyword/path and semantic reasons, line-level
  provenance, and top similarity `0.9616339092355713`.

## Knowledge promotion

- Evidence gate: `VERIFIED`
- Candidate: `60e1b128-33f7-4dbf-a0a2-bac6d341ae16`
- Canonical case: `2ca980f8-1dfa-40be-b7eb-65756cc6be78`
- Publish outcome: `CREATED_CANONICAL`
- Managed wiki:
  `/data/vault/_generated/Runbooks/Ollama-Embedding-CPU-Guard.md`

## Boundaries

- This is a bounded live verification, not a long-duration thermal or endurance test.
- The application cannot read the user's CPU package sensor through the available Windows ACPI
  provider. Temperature must be checked with the user's hardware monitor.
- Existing source files, backups, document history, embeddings, and audit rows were not deleted.
