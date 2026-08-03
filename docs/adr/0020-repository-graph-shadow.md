# ADR 0020: PostgreSQL repository graph shadow

Status: accepted for shadow evaluation; production promotion rejected on 2026-08-03

## Context

Repository search already combines lexical, pgvector, seeded-vector recovery, reward reranking,
current-revision provenance checks, and answer verification. Replacing this path with Graphify or a
separate graph database would discard working quality gates and add another mutable system of
record.

The existing `repository_source_relation` table is not a traversal graph. It stores source and
target strings, has no canonical target identifier or reverse index, and does not prevent duplicate
relations. More importantly, the bounded regex analyzer labels Java, TypeScript, shell, and other
regex matches as `STATIC_CONFIRMED`. A deterministic audit of 151,563 relations found large
ambiguous and unresolved populations. The ESB Java snapshot produces no edge eligible for the
strict graph gate.

## Decision

Keep PostgreSQL and the current retrieval path as the only production path. Add rebuildable graph
shadow tables with immutable, snapshot-scoped nodes and edges:

- nodes retain snapshot, relative path, content hash, qualified symbol, symbol span, signature, and
  a stable fingerprint;
- edges retain canonical node IDs when resolvable, relation, extractor and version, resolution and
  verification status, confidence, evidence, and a stable unique fingerprint;
- outgoing and reverse traversal indexes are present;
- regex, ambiguous, unresolved, global unqualified-name, and global unique-leaf guesses are
  navigation-only;
- the retrieval hard gate admits only current AST/runtime/manual evidence resolved as same-file
  exact, qualified exact, local receiver, or internal import path;
- direct relationship intent is limited to one hop; explicit flow or impact intent is limited to
  two hops, with an allowlist, bounded fan-out, hub penalty, and confidence decay;
- repository maps use a model-token budget. The default counter is a conservative UTF-8 byte upper
  bound; a model tokenizer can replace it without changing the budget contract;
- `LKP_REPOSITORY_GRAPH_SHADOW_ENABLED` is false by default. Shadow execution only logs aggregate
  comparison metrics and never changes the API response or ranking.

Graphify, SCIP, Neo4j, FalkorDB, Joern, hooks, and strict modes are not production dependencies.
Graphify and SCIP comparison may run only against a frozen snapshot after the edge precision gate
passes.

## Promotion gates

Promotion requires all of the following on at least three held-out repositories and 30 paired gold
cases using the same answer model and prompt:

- relationship-cohort Recall@5 improves by at least 8 percentage points;
- answer, citation, no-answer, and unsupported-claim rejection do not regress;
- uncached input tokens improve by at least 15%, or elapsed time by at least 10%;
- graph retrieval p95 is no more than 1.2 times baseline;
- manually verified exact/resolved edge precision is at least 98%.

The 2026-08-03 audit did not establish the last requirement. Promotion therefore remains rejected,
and the held-out model A/B is not a valid production approval artifact yet.

## Rollback

Keep the feature flag false to disable all runtime graph reads. If the schema itself must be
removed, downgrade Alembic from `0022_repository_graph_shadow` to
`0021_repository_read_indexes`; only rebuildable shadow rows are dropped. Existing relations,
knowledge, embeddings, search behavior, UI, and response contracts are unaffected.
