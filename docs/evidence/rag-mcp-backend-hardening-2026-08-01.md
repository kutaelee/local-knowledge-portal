# RAG, repository vector, and Codex MCP hardening — 2026-08-01

## Decision

The backend design has been corrected end to end and passes the pre-scale acceptance boundary while
preserving the existing UI and public API schema. Large-scale expansion remains conditional on a
separate held-out project set. All source, revision, project-scope, claim, and citation failures are
fail-closed; an unavailable or unsupported evidence set produces no-answer.

This report supersedes the retrieval-policy conclusion in
`codex-mcp-evaluation-2026-08-01.md`. That experiment measured low-confidence lexical navigation
while repository vectors were absent and therefore correctly rejected unconditional pre-search.
The applied policy is conditional retrieval for evidence-sensitive questions, followed by an
explicit verifier; it is not unconditional RAG injection.

## Verified design gaps

| Layer | Observed defect | Applied invariant |
| --- | --- | --- |
| embedding coverage | Current repository snapshots had `0 / 177` searchable knowledge vectors; nightly maintenance refreshed document chunks only. | One GPUQ task refreshes current document and repository vectors with one shared model load. |
| snapshot lifecycle | A new snapshot invalidated old vectors but did not generate replacements; `stale=false` alone did not prove newest revision. | Only the deterministic newest `(created_at, id)` snapshot is eligible, and stale/nonlatest vectors are pruned. |
| revision reversion | Reverting source to a previously analyzed hash reused the old snapshot without making it current/latest again. | Reused analysis is atomically reactivated, every competing snapshot is marked stale, and the reactivated snapshot becomes the sole deterministic latest revision. |
| source references | Several API/report/tool paths trusted reference shape or a stored hash without matching the current snapshot file and line bounds. | Every reference must match current `relative_path`, exact content hash, and valid line range; malformed/empty references fail closed. |
| candidate generation | Caller Top-K constrained the initial pool and could exclude verified failure cases before reranking. | Candidate generation is `min(200, max(80, Top-K * 10))`; hard gates run before reward-guided Top-K. |
| query-time GPU use | Hybrid queries could wake Ollama or wait for a timeout even when exact/current vectors already existed. | Query prewarm defaults off. Current lexical anchors seed vector expansion without loading a model. |
| evidence quality | Reported journals and derived summaries could crowd out source/verified evidence. | Reported/derived rows are navigation only; only high-confidence `source` or `verified` contexts can support answers. |
| context expansion | Adjacent raw chunks were appended after snippet redaction. | Expanded content is redacted before leaving the API. |
| MCP routing | The bridge collapsed intent, ignored repository analysis for architecture, and cached project snapshots for the process lifetime. | At most two bounded full-clause subqueries route across document, failure, and current repository ranges; project snapshots refresh per retrieval. |
| answer verification | The verifier was disconnected from MCP and accepted top-level text or numbers not represented by supported claims. | Up to three candidates are scored by claim/citation support; unsupported numbers and non-grounding evidence fail; one repair is allowed, then no-answer. |
| model lifecycle | Cleanup ran only after successful embedding requests and unload errors were swallowed. | Ownership is checked through `/api/ps`; a task-owned model is unloaded with `keep_alive=0`, verified, and cleanup failure fails the GPU job. |

## Processing flow

1. Read-only source scan creates an immutable repository snapshot and source hashes.
2. Static/model analysis emits knowledge items with exact file/hash/line references.
3. The current-reference policy rejects nonlatest, stale, rejected, malformed, hash-mismatched,
   or out-of-range rows before list, report, tool, search, evaluation, or embedding use.
4. The GPUQ nightly task waits for ingest quiescence and uses one `qwen3-embedding:0.6b` load for
   deferred document chunks, current repository items, knowledge deduplication, and the semantic
   recovery probe.
5. Query routing collects a wide lexical/semantic or lexical-seeded-vector pool, applies project,
   path, tag, provenance, and revision hard gates, then reward-reranks the surviving evidence.
6. Adjacent context is expanded and redacted with separate provenance.
7. Codex MCP selects the document, verified-failure, or repository-analysis range from the full
   question and returns a bounded evidence set. Reported/derived items remain navigation only.
8. `verify_answer` checks explicit claim/evidence-ID pairs, chooses the strongest candidate,
   permits one repair, and otherwise returns no-answer.
9. The embedding owner unloads and verifies its Ollama model; a model that was resident before the
   task is left untouched.

## Evaluation before the vector refresh

The original 84-case live corpus included 15 repository questions from a latest snapshot already
marked stale. Counting them as answerable understated both implementations. On the corrected
current corpus (27 repository questions, 42 verified-knowledge questions, and 8 no-answer cases):

| Metric | Original policy, corrected denominator | Hardened backend before new vectors | Delta |
| --- | ---: | ---: | ---: |
| Recall@5 | 0.8551 | 0.8986 | +0.0435 |
| Recall@10 | — | 0.8986 | — |
| MRR | 0.8262 | 0.8696 | +0.0433 |
| expected evidence alignment | — | 0.9516 | — |
| failure-case Top-3 | — | 0.9048 | — |
| no-answer accuracy | — | 1.0000 | — |
| citation validity | — | 1.0000 | — |

The six-case MCP suite covers representative architecture, representative failure, the same
symptom with a different cause, a boundary case, a verified success/A-B case, and intentional
no-answer. Before repository vectors it passed `6 / 6`; ESB architecture used keyword repository
retrieval and every answerable case passed the connected verifier. This result proves routing and
gating, not vector lift. The post-vector run below is the acceptance measurement.

## Post-vector acceptance evidence

GPUQ job `9c023199-6e0e-4ab5-a35b-ca66f0fa35bb` succeeded in 28.45 seconds. One model load served
all stages. Eighteen newly pending documents (186 embedding inputs) and all 177 eligible current
repository items were embedded; the job pruned 86 stale repository vectors. The recovery endpoint
then reported zero pending documents, chunks, and repository items.

| Metric | Before new repository vectors | Post-vector |
| --- | ---: | ---: |
| Recall@5 / Recall@10 | 0.8986 / 0.8986 | 0.8986 / 0.8986 |
| MRR | 0.8696 | 0.8768 |
| expected evidence alignment | 0.9516 | 0.9677 |
| failure-case Top-3 | 0.9048 | 0.9048 |
| no-answer accuracy | 1.0000 | 1.0000 |
| citation validity | 1.0000 | 1.0000 |

All six MCP cases passed again. Median bridge latency was 172.29 ms and maximum latency was
369.36 ms. The ESB architecture case changed from repository keyword retrieval to
`hybrid-seeded-vector`, returned five current source-verified contexts, and passed the connected
claim/citation verifier. The absent-protocol case returned strict no-answer with zero contexts.

The shared embedder processed 47.716 inputs/s and 10,008.79 prompt tokens/s. GPUQ observed a total
GPU-use peak of 7,481 MiB; the measured embedding-model increment remains about 6.2 GiB, not 25
GiB. The model was not resident before the task, `unload_verified` was true, and Ollama `/api/ps`
returned zero models after completion.

## Contract and verification

- UI files were not changed.
- Raw OpenAPI SHA-256 before deployment: `0895ce80c00fef0a141b5aced2cfc8962f86349daa67bfc758ed94c7478bef6d`.
- Raw OpenAPI SHA-256 after the latest image deployment:
  `0895ce80c00fef0a141b5aced2cfc8962f86349daa67bfc758ed94c7478bef6d` (unchanged).
- Unit suite: `272 passed`.
- Full default suite: `272 passed, 38 skipped`; PostgreSQL integration is run separately.
- Final isolated PostgreSQL integration: `38 passed`, including source-revision reactivation;
  disposable database `lkp_test_verify_20260801165545_43402` was dropped by its exit trap.
- Ruff and `git diff --check`: passed.
- Deployed API and worker image:
  `sha256:03b25046e29fbac75962642e0327ec3c46dd92fd3d4b9f1438e95d5942e2565a`.

## Expected value before large-scale expansion

The current corpus is generated from live portal evidence and is not a held-out multi-project
benchmark. A reasonable acceptance range for supported repositories remains Recall@5 `0.85–0.93`,
MRR `0.70–0.85`, failure Top-3 `>= 0.90`, no-answer accuracy `>= 0.95`, citation validity
`>= 0.98`, and unsupported-claim rejection `>= 0.95`. Expansion is a no-go if a held-out project
falls below any fail-closed or citation target even when aggregate recall rises.

## Rollback

- Restore the pre-change application image from
  `local-knowledge-portal-app:rollback-pre-rag-20260801-1543`.
- Restore the exact host configuration backup from
  `D:\LocalBackup\LocalKnowledgePortal\20260801-154125\codex-rag-mcp-config` if the query prewarm or
  MCP policy must also be reverted.
- Recreate only the affected Compose services. Embeddings and repository analyses are derived;
  source roots remain read-only and require no rollback.
- Revert the final Git commit if the code policy must be removed. No new public API schema or UI
  migration is required.

## Unresolved boundary

Portal-owned Ollama cleanup is now verified on success and failure. The workstation GPUQ scheduler
itself still has no generic success-path Ollama finalizer for arbitrary workloads; it cleans failed,
canceled, and timed-out jobs. That scheduler-wide safeguard is outside this repository change and
remains a workstation-level follow-up, not a reason to keep the portal model resident.
