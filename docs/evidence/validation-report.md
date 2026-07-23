# Validation report

Date: 2026-07-23, Asia/Seoul

## Executed and successful

- Read workstation filesystem policy before any write.
- Read-only drive/repository/vault discovery.
- `uv sync` with Python 3.13.14; generated `uv.lock`.
- `pnpm install --frozen-lockfile`; generated `pnpm-lock.yaml`.
- Pulled `pgvector/pgvector:0.8.2-pg18-trixie` at OCI digest `sha256:b7337db8fe39d12fe8ecb0003c72680f24479813a744b43154eee6f2eab5a5f3`.
- Started PostgreSQL 18.4 with data checksums on E: and loopback-only port 55432.
- `uv run alembic upgrade head`; current schema `0001_initial`.
- `uv run ruff check services scripts tests`: passed.
- `uv run pytest -m "not integration"`: 15 passed, 1 deselected after adding Codex capture
  filtering, redaction, idempotency, managed-page protection, and optional local-generation
  contract tests.
- Dedicated PostgreSQL integration database: 1 passed, 7 deselected. The fixture flowed through scanner, durable lease, worker, append-only version, four chunks, four 1024-dimensional deterministic test vectors, and the FastAPI keyword endpoint with provenance.
- `pnpm --filter @lkp/web build`: passed under Next.js 16.2.11.
- `docker compose --profile app build api`: passed.
- Playwright Chromium 149 stored under `E:\Cache\ms-playwright`; UI baseline: 1 passed.
- Live API returned `live`; readiness returned database true, schema `0001_initial`, Ollama false.
- Web returned HTTP 200 on `127.0.0.1:3010`.
- Main validation fixture returned one grounded keyword result at lines 10–13.
- Retrieval baseline: Hit@5 0.75, Hit@10 0.75, MRR 0.75, no-answer 1.0, citation structure 1.0.
- Created immutable backup `D:\Backups\LocalKnowledgePortal\database\2026-07-23T172113`.
- Restore test succeeded: 1 document, 4 chunks, 4 vectors, revision `0001_initial`, zero unvalidated foreign keys, content query count 1.
- Read the official Codex local-state and Hooks documentation before integrating session capture.
- Parsed the active transcript read-only from
  `%USERPROFILE%\.codex\sessions\2026\07\23\rollout-2026-07-23T16-52-36-019f8df6-4c53-7062-8aca-512840249201.jsonl`.
- Generated the managed page
  `E:\LocalKnowledgePortal\vault\_generated\codex-sessions\2026\07\23-019f8df6-4c53-7062-8aca-512840249201.md`.
- Verified its frontmatter has `managed: true`, the expected generator, source hash, session ID,
  and `codex-transcript-v1`; verified known system/tool/internal markers were absent.
- Indexed the active session as 38 lexical chunks with `embedding_status: pending`, without
  claiming an Ollama embedding was produced.
- `왜 구현안하고 멈춤` keyword search returned the active Codex session with provenance and
  high confidence.
- Started continuous capture as PID 29428 and observed a subsequent transcript update become a
  new managed page version.
- Installed a user-level Codex `Stop` hook at `%USERPROFILE%\.codex\hooks.json`; a new Codex
  session still requires the official `/hooks` trust review before that hook can run.
- A fresh dedicated database `lkp_codex_capture_validation_20260723` passed the PostgreSQL
  integration test: 1 passed, 15 deselected.
- Verified user-global polling by observing updates from two distinct Codex session IDs under the
  same Windows Codex home.
- Verified configuration accepts additional Codex homes for separately stored WSL sessions.
- Mocked the official Ollama `/api/tags` and `/api/chat` contracts: JSON-schema output,
  non-streaming mode, disabled thinking, temperature zero, structured response validation, model
  digest recording, and fail-closed digest mismatch all passed.

## Failed and corrected during validation

- PowerShell 5.1 lacked newer crypto/encoding helpers; bootstrap now uses compatible RNG and BOM-free UTF-8.
- PostgreSQL 18 requires its bind mount at `/var/lib/postgresql`; Compose was corrected before any database data existed.
- An internal Docker network prevented the loopback publication on this Docker Desktop setup; the network is now normal while the port remains bound only to `127.0.0.1`.
- Initial migration tsvector default compilation and nullable raw SQL parameter typing were corrected and revalidated.
- TypeScript 7 was not accepted by this Next.js build; the lock now pins stable TypeScript 5.9.3.
- Reusing the old `lkp_test` integration database initially leased a stale pending job, so the
  count assertion failed. No rows were deleted. Re-running against the fresh dedicated database
  passed.
- Direct `uv run` did not preserve Codex hook stdin on this Windows setup. The hook now invokes a
  PowerShell wrapper that reads and validates `transcript_path` before starting the Python
  capture; the wrapper is tested separately.

## Skipped or not proven

- Ollama connectivity/model download/model digest and production semantic quality.
- Actual local chat-model generation; the adapter was contract-tested without representing a
  mocked response as a real model run.
- Full source-root scan, watcher endurance/suspend test, worker crash/lease-expiry timing test, database restart test, and all data-dependent E2E scenarios.
- Scheduled tasks and optional MCP.
- Codex hook trust cannot be asserted programmatically; `/hooks` review is intentionally left to
  the user in a new session. The already running polling capture does not depend on hook trust.

## Current state

- PostgreSQL, FastAPI on `127.0.0.1:8010`, and Next.js on `127.0.0.1:3010` were running at the end of validation.
- Ollama readiness is false.
- Production DB contains the isolated validation fixture and the managed active Codex session.
  No existing project or transcript source was modified.
- Embedding test data is revision `validation-deterministic-d1024-v1`; the configured production revision remains `ollama-qwen3-embedding-0.6b-d1024-v1`.
- Codex capture is running and polls every five seconds. It reads existing transcript files but
  initially imports only the most recently active one; historical sessions are not bulk imported.

## Rollback

Stop API/web and run `scripts/stop.ps1`. No source rollback is required. Preserve or set aside `E:\LocalKnowledgePortal\postgres`; do not delete it automatically. Restore only into a new database after checksum and restore-test validation.
