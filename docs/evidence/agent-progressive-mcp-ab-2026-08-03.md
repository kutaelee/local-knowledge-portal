# Progressive MCP backend evaluation — 2026-08-03

## 1. Security-boundary verdict and evidence

**Synthetic fail-closed paths: PASS. Full data-plane clearance: NOT CLAIMED.**

- No model, GPU, production database, repository-content upload, remote API, or graph subsystem was used.
- The prohibited-project boundary was exercised only with synthetic fixtures. Unit verification passed
  `44/44`; the isolated security integration test passed `1/1`, and its dedicated database was removed
  by the exit trap.
- Denied MCP requests make zero portal calls; ingestion/session/hook guards, project scope, current
  revision, provenance, source hash, citation, unsupported-claim, and no-answer gates remain fail-closed.
- Repository graph retrieval remains policy-locked off. All new production-path flags default to false.
- Previously reported quarantined tracked/cache locations were not opened or reprocessed. Consequently,
  absence of real prohibited-derived artifacts across every durable store is not asserted by this run.

## 2. Measured baseline

Harness: `agent-task-ab-v2-exact-usage`; fixture SHA-256
`79457c9baa2d8df2aefa50af2f15a3c0be77b84cd8e7b24ae5dc6bc4c48e0cdb`; 14 held-out
synthetic tasks.

| Metric | Source-only | Current conditional MCP |
|---|---:|---:|
| Verified tasks | 7/14 | 13/14 |
| Answer quality | 50.00% | 92.86% |
| First-pass success | 50.00% | 85.71% |
| Citation validity | 22.22% | 100.00% |
| No-answer accuracy | 100.00% | 80.00% |
| Unsupported-claim rejection | 100.00% | 100.00% |
| Retrieval-call precision | 100.00% | 100.00% |
| Context utilization | 100.00% | 58.82% |
| Retrieval regret | 50.00% | 0.00% |
| Source-only portal-call rate | 0.00% | 0.00% |
| Diagnostic estimated uncached tokens / verified task | 406.14 | 395.38 |
| Diagnostic estimated total tokens / verified task | 646.14 | 524.62 |
| Tool calls / verified task | 2.143 | 2.615 |

Token counts above are explicitly labeled estimates and cannot satisfy the acceptance efficiency gate.

## 3. Implemented changes

All changes are backend-only and behind default-off feature flags.

- Query-focused **extractive** evidence compression selects original spans, preserves their source order,
  keeps exact paths/symbols/numbers atomic even at a tiny budget, and records any explicit atomic-anchor
  budget overflow. This follows the selective,
  query-focused direction of [RECOMP](https://arxiv.org/abs/2310.04408) without introducing a generator.
- Token-aware selection now uses a bounded relevance/evidence-strength/token-cost score plus
  [MMR](https://aclanthology.org/X98-1025/) novelty. Exact-value conflicts are exempt from duplicate
  suppression. This guard is important because a reported production reranker experiment found that
  reranking could lower NDCG and its best configuration improved only marginally
  ([community case](https://www.reddit.com/r/Rag/comments/1vbnqj3/our_reranker_was_making_retrieval_worse_so_we/)).
- Conflicting valid answer candidates fail closed. Visible competing exact values are protected from
  early-stop pruning; if Top-K or the token budget would omit a competing exact value, retrieval itself
  returns no-answer. A detected conflict cannot be bypassed through the one-repair path. The conservative
  choice is supported by [ConfRAG](https://aclanthology.org/2026.acl-long.11/), which reports explicit
  contradictions in 57.2% of its benchmark questions.
- Claim verification now checks exact identifiers in addition to numbers, lexical support, evidence level,
  citations, and answer-to-claim coverage. This is aligned with claim-level diagnosis in
  [RAGChecker](https://github.com/amazon-science/RAGChecker) and lightweight provenance checking
  ([EMNLP Industry 2024](https://aclanthology.org/2024.emnlp-industry.97/)).
- Compact evidence avoids burying relevant passages in long contexts, a failure mode documented by
  [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/).
- A content-free exact-usage importer accepts versioned OpenTelemetry-compatible GenAI token fields,
  explicit cache counts/status, model latency, end-to-end task elapsed time, verified outcome, and tool
  calls. It rejects content fields, untyped numeric strings, booleans as integers, negative values,
  incomplete cache provenance, duplicate/conflicting spans, unpaired tasks, model mismatches, and outcome
  mismatches. Missing counts are never estimated, matching the
  [OpenTelemetry GenAI metrics convention](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/gen-ai-metrics.md).
- The A/B harness no longer presents estimator output as measured token efficiency. Acceptance is closed
  unless all held-out tasks and all three variants have complete exact telemetry.

## 4. Paired A/B results

| Metric | Current conditional MCP | Improved progressive MCP | Change |
|---|---:|---:|---:|
| Verified tasks | 13/14 | 14/14 | +1 task |
| Answer quality | 92.86% | 100.00% | +7.14 pp |
| First-pass success | 85.71% | 92.86% | +7.15 pp |
| Citation validity | 100.00% | 100.00% | no regression |
| No-answer accuracy | 80.00% | 100.00% | +20.00 pp |
| Unsupported-claim rejection | 100.00% | 100.00% | no regression |
| Retrieval-call precision | 100.00% | 100.00% | no regression |
| Context utilization | 58.82% | 64.29% | +5.47 pp |
| Retrieval regret | 0.00% | 0.00% | no regression |
| Source-only portal-call rate | 0.00% | 0.00% | gate passed |
| Diagnostic estimated uncached tokens / verified task | 395.38 | 222.00 | -43.85% |
| Diagnostic estimated total tokens / verified task | 524.62 | 342.00 | -34.81% |
| Tool calls / verified task | 2.615 | 2.429 | -7.11% |

There were zero paired quality regressions. The estimator indicates a promising token direction, but exact
uncached/total tokens and end-to-end elapsed time remain unmeasured. Sub-millisecond deterministic harness
runtime is not a model or agent latency measurement and is excluded from acceptance.

An independent read-only Luna recheck passed all 44 targeted units and the three adversarial regressions:
tiny-budget late-anchor preservation, nested content-telemetry rejection, and Top-K=1 conflict fail-closed.
It used no network, model, GPU, or production database.

## 5. Go/No-Go decision

**NO-GO / REWORK for production enablement.**

Quality, citation, no-answer, unsupported-claim, source-only-call, utilization, regression, and synthetic
security gates pass. The mandatory exact efficiency measurement gate does not pass because no complete
paired content-free usage trace was supplied. Existing production behavior remains active and all new
flags remain false.

## 6. Rollback procedure

1. Keep `LKP_MCP_QUERY_FOCUSED_COMPRESSION_ENABLED`, `LKP_MCP_MMR_ENABLED`, and
   `LKP_MCP_CONFLICT_GATE_ENABLED` false; this is the current default and preserves the existing path.
2. If experimental flags were enabled in an isolated environment, set them false and restart only the MCP
   bridge/API process using the normal service lifecycle.
3. Revert the implementation commit if code rollback is required. No schema migration, production data
   mutation, graph index, model artifact, or deployment must be undone.

## 7. Unresolved risks requiring approval

- Capture a complete paired exact-usage trace from the same local model/runtime for every held-out task and
  all three variants; repository/prompt/response content must remain absent from telemetry.
- The 14-task synthetic set is intentionally small. Approval is required before expanding it with additional
  non-sensitive repository tasks or running a model-backed evaluation.
- `MMR lambda=0.72`, a 320-token harness budget, compression limits, and visible-conflict context retention
  are conservative starting values, not live-data optima.
- Exact-identifier extraction covers common paths, symbols, uppercase constants, and numeric values; uncommon
  language-specific identifier forms need additional synthetic cases before enablement.
- A full real durable-store exposure audit was not performed because the security boundary prohibits opening
  or processing quarantined prohibited-derived content. Any such audit requires explicit, narrowly scoped
  approval and a non-content inspection procedure.
