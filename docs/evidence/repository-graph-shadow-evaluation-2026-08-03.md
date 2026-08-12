# Repository graph shadow evaluation — 2026-08-03

## Final decision

**NO-GO / REWORK.** The PostgreSQL graph shadow is implemented and tested, but production graph
retrieval remains disabled. The existing lexical + pgvector + seeded-vector + reward reranker +
revision/provenance gate + answer verifier path is unchanged.

The blocker is evidence quality, not graph storage. The legacy relation population cannot satisfy
the 98% exact/resolved precision gate from the available frozen evidence, and the ESB Java graph
has zero edges eligible for strict retrieval.

## Reproduction identity

- repository: `/home/kutae/src/local-knowledge-portal`
- branch: `agent/codex-wiki-capture`
- starting commit: `1cfc0c92ec8b0671d85248308850976ab7b399f4`
- production DB writes: none
- production deployment: none
- schema observed at `2026-08-03 07:11:42.439067+00`: `0021_repository_read_indexes`
- graph migration created but only exercised in a disposable integration database:
  `0022_repository_graph_shadow`

The SQL used to recapture counts is in `scripts/audit_repository_graph_baseline.sql`.

## Frozen baseline counts

Captured at `2026-08-03 07:11:42+00`:

| Project | Snapshot | Files | Symbols | Relations | Knowledge |
|---|---|---:|---:|---:|---:|
| esb | `2795cffc-577b-52bf-a3ec-51fd2a33ad67` | 2,969 | 44,488 | 131,702 | 157 |
| local-knowledge-portal | `37378b7a-577e-5484-b677-817b048afeb7` | 217 | 3,480 | 18,185 | 102 |
| repository_analysis | `728ca59d-f6cf-55a2-905c-7522150bfbbd` | 14 | 146 | 1,676 | 20 |

Total relations: **151,563**. All 34 observed language/relation strata used the legacy value
`STATIC_CONFIRMED`, including bounded-regex Java, TypeScript, and shell output. There were 850
duplicate legacy fingerprints, representing 927 redundant rows; the maximum identical copy count
was four.

The accepted non-graph report at commit ancestry remains:

| Metric | Existing result |
|---|---:|
| Recall@5 / Recall@10 | 0.8986 / 0.8986 |
| MRR after vector completion | 0.8768 |
| evidence alignment | 0.9677 |
| failure retrieval Top-3 | 0.9048 |
| no-answer accuracy | 1.0000 |
| citation correctness | 1.0000 |

These numbers come from `rag-mcp-backend-hardening-2026-08-01.md`. They were not relabelled as a
new held-out run. A fresh semantic baseline was not run because it would load the embedding model
outside an admitted GPU job, and the local-knowledge-portal snapshot became revision-stale while
this branch changed. Therefore the requested fresh baseline gate is **not satisfied**.

## Relation quality audit

Command: deterministic `repository-relation-audit-v1`, fixed three snapshot IDs, 200 rows sampled
across project, language, relation type, and provenance.

| Outcome | Count |
|---|---:|
| ambiguous target | 48 |
| unresolved/dangling target | 111 |
| exact local | 7 |
| exact unqualified global | 3 |
| resolved local receiver | 12 |
| resolved unique leaf | 19 |
| duplicate rows in sample | 2 |
| self-loops | 7 |
| AST-parsed | 94 |
| regex-inferred | 106 |
| strict eligible | 4 |

The four strict-eligible rows were source-line supported, but four is not enough to establish 98%
precision. A second 200-row sample was drawn only from the 1,852 strict-eligible rows. It contained
146 exact-local, 47 internal-import, and 7 local-receiver resolutions. A total of 190 rows matched
their current source line. Ten referenced files whose current content hash no longer matched the
frozen snapshot, so they correctly fail closed. The current-source proxy is therefore 190/190, but
the frozen 200-row manual semantic precision requirement is still **not established**. A line-text
proxy must not be reported as manual target precision.

## Graph build dry-run

No graph rows were written to production.

| Snapshot scope | Deduplicated edges | Eligible | Navigation-only |
|---|---:|---:|---:|
| fixed local-knowledge-portal | 18,036 | 1,687 | 16,349 |
| current ESB | 130,798 | 0 | 130,798 |
| current repository-analysis subproject | 1,791 | 178 | 1,613 |

The ESB result is intentional. Its Java edges come from the bounded regex extractor and are stored
as `REGEX_INFERRED`, never `STATIC_CONFIRMED`, in the new graph layer. Spring/XML/JMS/JAR adapters
or runtime traces are required before ESB relationship retrieval can be promoted.

## Implementation

- stable graph node and edge schema with unique fingerprints;
- outgoing, reverse, and hard-gate indexes;
- extractor and extractor-version provenance;
- explicit resolution and verification statuses;
- ambiguous, unresolved, regex, unqualified-global, and unique-leaf guesses retained only for
  navigation/audit;
- relationship intent classifier: one hop for direct relations, two for explicit flow/impact;
- relation allowlist, fan-out bound, hub penalty, confidence decay, and cycle suppression;
- query-specific repository map with path, signature/span, and edge chain first;
- conservative token upper-bound budget, not a character limit;
- feature flag OFF by default and no API response changes;
- dry-run backfill and deterministic audit commands.

## Verification

- focused unit tests: `5 passed`
- disposable PostgreSQL integration suite: `38 passed`, one upstream deprecation warning
- migration and graph persistence were exercised only in database
  `lkp_test_verify_20260803163240_17234`, which the exit trap dropped successfully
- production Alembic revision remained `0021_repository_read_indexes`

## Held-out and external analyzer A/B

Status: **not an approval artifact**.

SCIP and pinned Graphify were not installed or run. The prerequisite edge gate failed first, so
adding external analyzers would create cost without making the current graph promotable. No
three-repository/30-case paired answer-model A/B is claimed. Consequently, Recall@5 `+8pp`, token
`-15%` or elapsed `-10%`, and graph p95 `<=1.2x` baseline are all **unproven**.

Because the feature flag is off and shadow output is excluded from answers, current production
answer, citation, no-answer, and unsupported-claim behavior cannot regress from this change.

## Pre-scale expected value

The honest expected production Eval increase at this checkpoint is **0** because graph retrieval is
disabled. For the shadow cohort, only 1,852 of 151,563 fixed relations (about 1.2%) enter the strict
eligible pool, and none are from ESB Java. There is no defensible basis yet for the previously
suggested broad Recall or token gains. The next measurable upside is limited to Python
same-file/import relationship queries after a frozen manual precision set is created.

## Rollback

1. Keep `LKP_REPOSITORY_GRAPH_SHADOW_ENABLED=false` (default) to disable all runtime graph reads.
2. If migration 0022 is ever applied in a test or later deployment, downgrade to
   `0021_repository_read_indexes` to drop only graph shadow tables.
3. Remove the graph builder/backfill modules if the experiment is abandoned. Existing source
   relations, repository knowledge, vectors, UI, API schemas, and response payloads are untouched.

## Required rework before another Go/No-Go

1. Freeze source content for the audited rows or retain immutable source excerpts sufficient for
   manual edge adjudication.
2. Add Python import/alias and receiver-type resolution; keep unique-leaf resolution navigation-only.
3. Add ESB-specific Spring XML, JMS destination, configuration binding, JAR/decompiled-symbol, and
   optional runtime-trace adapters.
4. Build at least three genuinely held-out repositories and 30 gold cases covering direct call,
   reverse impact, config-to-code, lifecycle, same-symbol/different-cause, dynamic/DI, and no-path.
5. Only after exact/resolved precision reaches 98%, run current analyzer vs SCIP vs pinned Graphify
   on the same frozen snapshots, then run the paired same-model answer A/B.
