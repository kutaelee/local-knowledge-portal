# ADR 0009: Canonical knowledge-case materialization

- Status: Accepted
- Date: 2026-07-24

## Context

Verified cases were stored in PostgreSQL while searchable Markdown pages were created by a
separate manual process. The two representations drifted: an Ollama case retained an obsolete
two-CPU resolution after the operational default moved to one CPU, and legacy Korean pages were
written with broken encoding. A newly verified startup incident did not enter vector retrieval at
all.

## Decision

`knowledge_case` and its append-only revisions remain the canonical record. Publishing a
verified candidate now deterministically materializes the current case into
`_generated/Knowledge-Cases`, records the projection in `generated_page`, and enqueues a
priority indexing job. The page is UTF-8, contains the current problem, symptom, root cause,
resolution, verified evidence, case ID, revision, and content hash.

Changing established guidance requires a verified candidate with an explicit
`supersedes_case_id`. It appends a revision and occurrence, updates the current canonical view,
and preserves the previous dedup key as metadata. It does not silently rewrite history.

Legacy manually generated Runbooks are excluded from active retrieval after the canonical cases
are materialized. Their files and historical document versions are retained; they are not deleted.
Only active canonical pages participate in lexical, semantic, hybrid, and RAG retrieval.

## Consequences

- A published case is searchable without a second manual wiki-writing step.
- Current retrieval cannot return both superseded and current operational advice.
- Every result retains normal document/version/chunk/line provenance plus the case and revision
  identifiers in the managed page.
- Activity history still does not automatically become a case. Category evidence gates and
  explicit publication remain mandatory.
- A failed materialization fails the publish request instead of reporting a searchable case that
  was never projected.
