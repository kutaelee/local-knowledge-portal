# Retained-state and compaction assessment

Date: 2026-08-04

## Decision

`NO-GO / REWORK` for evidence-based broad promotion. A workstation-local canary was explicitly
enabled by the user on 2026-08-04 so real usage can be collected before the later promotion
decision.

The direction is useful, but only the deterministic synthetic evaluation is complete. The required
paired exact token and elapsed-time trace has not been supplied, so the efficiency acceptance gate
cannot pass.

## Applicability

OpenAI reported that replacing a harness which discarded reasoning and used rolling truncation with
Responses API state chaining and compaction improved its ARC-AGI-3 public-set score from 13.3% to
38.3% while using six times fewer output tokens. This is task- and harness-specific evidence, not a
transferable portal performance estimate.

- Codex already uses retained reasoning and automatic history compaction. The workstation config
  leaves the compaction threshold and prompt unset, so model-aware defaults remain active. Adding an
  undocumented retained-reasoning key, copying the ARC threshold, or replacing the built-in compact
  prompt would be an unmeasured override and was not applied.
- The portal is a local evidence provider, not an OpenAI Responses API loop. It must not store model
  chain-of-thought. The applicable design is to separate transcript from verified retrieval state:
  retain a small evidence fingerprint, return compact evidence cards, and invalidate retained state
  whenever project, revision, source hash, or evidence body changes.

Official references:

- <https://openai.com/index/how-two-settings-tripled-our-arc-agi-3-scores/>
- <https://developers.openai.com/api/docs/guides/compaction>

## Implemented behind existing feature flags

- Added a hashed, revision-aware session evidence fingerprint. It contains no evidence body and no
  model reasoning.
- Changed progressive selection and session reuse to key on that fingerprint instead of citation ID
  alone. A stable citation ID can no longer hide a changed revision or changed body.
- Added `retained_state_hits` and `estimated_retained_state_tokens_saved` diagnostics to the MCP and
  agent-task A/B harness.
- Added daily append-only, content-free live telemetry and an offline summarizer. It records feature
  flags, counters, timings, compact-state savings, and verifier outcomes only.
- Added synthetic regression tests for identical-state reuse, revision invalidation, body-change
  invalidation, and retained-state savings.
- Kept progressive MCP and session dedup disabled by default.

## Reproducible synthetic result

Command:

```text
uv run python scripts/run_agent_task_ab.py
```

Held-out tasks: 14

| Metric | Current conditional MCP | Improved progressive MCP |
| --- | ---: | ---: |
| Verified tasks | 13 | 14 |
| Answer quality | 0.9286 | 1.0000 |
| Citation validity | 1.0000 | 1.0000 |
| No-answer accuracy | 0.8000 | 1.0000 |
| Unsupported-claim rejection | 1.0000 | 1.0000 |
| Source-only portal call rate | 0.0000 | 0.0000 |
| Context utilization | 0.5882 | 0.6429 |
| Estimated uncached tokens / verified task | 395.38 | 222.00 |
| Retained-state hits | 0 | 1 |
| Estimated retained-state evidence tokens saved | 0 | 158 |

The combined progressive path shows a 43.85% deterministic estimated reduction in uncached tokens
per verified task with no synthetic quality regression. This estimate is diagnostic only. The
microbenchmark's Python elapsed time is not a model or end-to-end latency measurement.

## Acceptance status

- Quality, citation, no-answer, unsupported-claim, source-only-call, context-utilization, and
  synthetic security gates: pass.
- Exact paired uncached-token or elapsed-time improvement: not measured.
- Broad promotion: blocked by the exact-efficiency gate. The local canary is active by explicit user
  direction and does not weaken any evidence or security gate.

## Local canary collection

- Operational API and Codex MCP progressive flags: enabled.
- Low-confidence navigation and repository graph retrieval: disabled.
- Telemetry: `E:\Data\LocalKnowledgePortal\metrics\mcp\mcp-usage-YYYY-MM-DD.jsonl`.
- First synthetic source-first event: recorded successfully without an API, embedding, or model
  call.
- Summary command:

```text
uv run python scripts/summarize_mcp_telemetry.py --directory /mnt/e/Data/LocalKnowledgePortal/metrics/mcp
```

The live stream can measure retrieval behavior and verified-answer outcomes. Exact uncached model
tokens still require a paired Codex usage trace; the live MCP file does not contain prompts,
answers, evidence, citations, or project keys.

## Rollback

1. Restore `C:\Docker\local-knowledge-portal\.env.20260804-020125.bak` and
   `C:\Users\kutae\.codex\config.toml.20260804-020125.bak` if the canary must be disabled.
2. Recreate only the API service from WSL so Linux bind mounts remain valid.
3. Revert the retained-state fingerprint and diagnostic changes if they cause a test or integration
   regression. No database migration or data rollback is required.
4. The Codex model compaction defaults remain unchanged.
