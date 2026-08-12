# Codex MCP retrieval evaluation — 2026-08-01

## Decision

The read-only Local Knowledge Portal MCP is installed and usable, but automatic pre-search is
**rejected**. Codex uses it only when the user explicitly requests portal evidence or after one
bounded current-source search misses. This preserves access to indexed history without adding RAG
context to tasks that current source already answers.

## Test boundary

- Evaluation project: `local-voice-agent`, not the portal repository.
- The worktree was clean and its latest commit was 2026-07-26; the portal exposed 54 indexed
  documents for that project.
- Both variants used fresh ephemeral Codex sessions, the same prompt and model, a read-only sandbox,
  and no file changes.
- Raw local-only JSONL and final answers are under
  `E:\Data\LocalKnowledgePortal\evaluations\codex-mcp-ab-20260801T1400` and are not committed.
- The semantic circuit was fail-closed as `gpu_recovery_pending` with 10 documents pending. These
  runs therefore evaluated lexical fallback and low-confidence navigation, not high-confidence
  semantic context.

## A/B observations

| Question cohort | Variant | Elapsed | Input tokens | Tool calls | Result |
| --- | --- | ---: | ---: | ---: | --- |
| MTP/runtime decision | MCP off | 247.0 s | 1,131,640 | 35 | Correct, cited |
| MTP/runtime decision | low context on | 274.0 s | 1,096,266 | 23 | Correct, cited; slower |
| Tool Executor boundary | MCP off | 282.0 s | 1,375,124 | 21 | Correct, cited |
| Tool Executor boundary | low navigation on | 328.0 s | 2,045,856 | 58 | Correct, cited; worse |
| PowerShell Korean failure | MCP off | 314.4 s | 2,100,917 | 46 | Correct, cited |
| PowerShell Korean failure | low navigation on | 370.1 s | 2,219,337 | 53 | Correct, cited; worse |

For the failure cohort the MCP call itself took 146 ms and returned 1,045 navigation characters,
but the complete task took 17.7% longer, used 5.6% more input tokens, and made seven more tool calls.
For the Tool Executor cohort, elapsed time increased 16.3% and input tokens increased 48.8%.
The low-confidence failure answer was slightly more careful not to invent the exact PowerShell
cmdlet or code page, but both variants reached the same supported cause, fix, and verification
boundary. That small wording benefit does not justify automatic pre-search.

One run that stopped early because the Windows read-only sandbox could not load a mandatory skill
was excluded. An earlier no-context run was also excluded from the positive decision because it did
not demonstrate use of indexed content.

## Applied efficiency gates

The bridge now:

1. splits at most three independent intents and searches them concurrently;
2. prefers distinctive identifiers such as exact model, error, or symbol names;
3. caps high-confidence output at five contexts and 6,000 characters;
4. strips low-confidence bodies to at most five 240-character navigation hints;
5. keeps low-confidence and fallback results at `no_answer=true`;
6. exposes only read-only, idempotent tools and remains optional at Codex startup.

Automatic pre-search may be reconsidered only after the semantic circuit is healthy and repeated
held-out runs show both non-inferior answer/citation quality and at least 10% elapsed-time or 15%
uncached-input-token improvement. Until then the source-first fallback is the final setting.
