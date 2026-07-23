# ADR 0006: Knowledge-value selection before durable activity and embedding

Status: accepted

## Context

The first implementation treated every supported text file and every accepted Codex hook event as
potential knowledge. This was safe for source preservation but operationally incorrect: generated
tokenizer payloads produced tens of thousands of low-value records, one 524 KB `merges.txt` held a
worker for more than 34 minutes, and lifecycle/read-only hook events added activity noise.

Readable input is not automatically reusable knowledge. The system needs an explicit reason to
retain an activity or create a semantic vector.

## Decision

The pipeline has four distinct promotion boundaries:

1. **Raw transport envelope**: bounded, redacted, atomic spool used only for outage recovery and
   collector idempotency.
2. **Activity history**: deterministic signal selection retains meaningful user instructions,
   changed files, command failures, verification/build/test/backup operations, and reported
   outcomes. Session lifecycle events, acknowledgements, and read-only inspection tools remain
   `filtered_low_signal` and do not become `activity_event` rows.
3. **Searchable document**: supported source files that are not ignored become versioned lexical
   chunks. Generated tokenizer payloads are ignored. Lockfiles, minified/generated files, and
   documents over the semantic cost budget remain lexical-only.
4. **Canonical knowledge case**: only evidence-gated candidates become reusable cases. Reported
   success without execution evidence remains unverified.

Selection is deterministic and versioned as `deterministic-knowledge-value-v1`. An LLM does not
decide whether ingestion succeeds. Ambiguous material is retained lexically or marked for review,
not silently promoted.

The semantic budget defaults to 128 chunks and 250,000 characters per document. Exceeding either
limit records `embedding_status=skipped_cost_limit` and the exact reason while preserving lexical
search and provenance.

## Queue implications

One file remains one durable index job because this is the correct idempotency, version, retry, and
provenance boundary. Small files are not combined into an opaque batch. Expensive work is bounded
inside that unit, live watcher jobs retain higher priority, and partial indexes support ready-job
claiming and expired-lease recovery as history grows. Completed jobs are cumulative audit history,
not active backlog.

## Consequences

- Initial scans drain predictably without letting one generated artifact monopolize Ollama.
- Keyword/path search remains available for lexical-only documents.
- Semantic coverage intentionally excludes machine-generated dependency/model payloads.
- The portal must expose selection reasons, active backlog, recent throughput, and estimated drain
  time instead of presenting cumulative completed history as queue pressure.
- Policy changes require a pipeline revision and retrieval evaluation before broader inclusion.
