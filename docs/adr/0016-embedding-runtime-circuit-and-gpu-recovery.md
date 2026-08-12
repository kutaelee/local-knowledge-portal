# 0016 — CPU embedding circuit and GPU-scheduled recovery

## Status

Accepted on 2026-07-25.

## Context

The CPU-only `qwen3-embedding:0.6b` service is deliberately capped at 0.5 CPU to avoid the
previous workstation thermal incident. In production observation, even a roughly 228-token
request exceeded the 90-second request budget. Continuing automatic retries would consume the
same CPU capacity, inflate job failures, and make semantic/hybrid search appear available when it
was not.

The workstation already has a GPU reservation authority (`gpuq`). Persistent GPU access from the
portal would bypass its fairness and VRAM safety policy.

## Decision

1. Treat source files, versions, chunks, lexical vectors, and provenance as the minimum durable
   ingestion result. Embeddings are derived and optional.
2. Keep generated evidence in `semantic_exclude_patterns`; it remains discoverable by keyword and
   path, but does not crowd the vector budget.
3. Limit only the derived embedding input to 2,400 characters, retaining the original chunk for
   citation and lexical retrieval.
4. Open a one-hour timeout circuit after three recent Ollama `ReadTimeout` events. While open,
   index documents as `deferred_runtime` and degrade semantic/hybrid requests to keyword-only with
   an explicit response mode. Do not prewarm the query embedding model at API startup.
5. Preserve recovered error details on the job row rather than leaving a successful job labelled
   with a current error.
6. Resume semantic indexing only with a measured, scheduler-aware GPU batch/reindex path submitted
   through `gpuq`; a persistent `gpus: all` CPU-embedding service is not an acceptable shortcut.
   The first production measurement of `qwen3-embedding:0.6b` added about 6.2 GiB of VRAM, so the
   recovery wrapper reserves 8 GiB rather than the invalid 2 GiB trial estimate.

## Consequences

The portal remains useful and truthful during an embedding outage: explorer, document history,
keyword/path/symbol search, citations, project journals, and operations remain available. Semantic
coverage can be intentionally low, and the dashboard exposes the circuit state instead of hiding
it. A future GPU reindex must use a new or explicitly selected embedding revision and must not mix
vectors from revisions or dimensions.

The one-shot recovery service uses the private Compose `postgres` hostname and a temporary
`ollama-embedding-batch` service. It never reads the Windows host-only database endpoint from the
operational environment file. The temporary service is stopped by the runner on success, failure,
or scheduler cancellation.
