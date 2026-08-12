# ADR 0010: Global Codex activity promotion into evidence-gated knowledge

- Status: Accepted
- Date: 2026-07-24

## Context

The global Codex hooks were collecting activity from several concurrent chat sessions, but only
cases manually created during the portal implementation reached `knowledge_case`. Production had
activities from four session workspaces and hundreds of exit-code-backed events while canonical
knowledge remained at three manually curated cases. This made the portal appear scoped to the
current chat even though the transport was global.

The original Korean instruction classifier also retained too few user instructions. In addition,
the chat workspace directory was shown as the project even when changed files clearly belonged to
another repository. A reported Stop message cannot safely solve either problem by itself because
an assistant success statement is not execution evidence.

## Decision

The hook collector now runs a deterministic activity-to-knowledge finalizer across every captured
Codex session:

1. A turn remains activity-only unless it has a non-empty Stop report, at least one successful
   mutation of a meaningful source/configuration/document file, and at least one successful
   test, lint, validation, or build command with an observed exit code.
2. The repository project is derived from changed paths below `C:\Dev\Repos\<project>` or
   `/home/<user>/src/<project>`. The chat workspace name is only a fallback.
3. The Stop report is retained as `reported_result` and rendered under an explicit
   **Reported outcome** heading. It is never counted as verified evidence.
4. File mutations and command exits become separate `EvidenceRecord` rows. The existing category
   gate must reach `VERIFIED` before publication.
5. A completed, evidence-backed turn may publish automatically. Reports led by `partial`,
   `진행 중`, `진행 상황`, or equivalent incomplete-state wording remain review candidates even
   when their completed substeps have evidence.
6. Reprocessing the same Stop is idempotent through `source_stop_activity_id`. Canonical dedup,
   occurrence counting, revision history, same-symptom relationships, and `NEEDS_REVIEW` behavior
   remain authoritative.
7. A delayed PostToolUse event reopens a Stop previously classified activity-only for missing
   change or validation evidence. This prevents hook delivery ordering from permanently losing a
   valid case.
8. Canonical materialization and the watcher use the same file metadata idempotency key. Writing
   one managed page therefore creates at most one effective index job.

The collector reads only a bounded tail of the read-only Codex transcript when a retained prompt
is unavailable. It does not scan an entire long transcript on every polling cycle.

An optional local LLM may later enrich candidate wording through the existing generation adapter,
but it must be disabled by default and may not create evidence, change verification state, bypass
deduplication, or publish a case that the deterministic gate rejects.

## Consequences

- Development knowledge can accumulate from other Codex chats without embedding ordinary reads,
  acknowledgements, screenshots, or unverified success reports.
- Incomplete work remains visible for review but does not become a canonical fact.
- Human-readable pages distinguish reported narrative from observed execution evidence.
- Automatic cases initially favor reproducible implementation records. Error-resolution,
  performance, and operations cases still need their stricter category-specific evidence.
- Classifier or extraction changes require an explicit extractor revision and regression tests;
  they do not force unrelated source documents through the embedding pipeline.
