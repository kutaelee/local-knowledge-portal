# Backend RAG pre-scale validation — 2026-07-27

## Decision

The backend is ready for pre-scale use with a pass. Retrieval,
fail-closed provenance, answer verification, lexical fallback, and rollback
are implemented and running without UI or external response-schema changes.

## Measured gaps and changes

- Semantic coverage was `1,751 / 169,219` chunks (`1.03%`), with the runtime
  circuit held open as `deferred_gpu_recovery`.
- Repository keyword tokenization used an unsafe escaped whitespace expression;
  multi-word questions could fail to expose exact symbol candidates.
- Repository retrieval searched summaries but not the source-symbol index.
- Hybrid repository search waited for query embedding during GPU contention.
- General retrieval lacked automatic project/failure scope, wide candidate
  generation, reward reranking, adjacent context, and a strict no-answer gate.
- Answer generation validated references only after generation and did not
  repair a schema-valid but unsupported answer.

The deployed policy now:

1. collects `max(30, Top-K * 8)` candidates, with project/failure routing;
2. fails closed on stale snapshot, project mismatch, revision mismatch, invalid
   source hash/line citation, and unsupported claims;
3. combines lexical, semantic, scope, evidence, identifier, knowledge-type, and
   validation rewards;
4. rejects general candidates below query-dependent thresholds and repository
   candidates below `0.24` unless an exact identifier anchors them;
5. selects at most two chunks per document and expands adjacent chunks with
   separate provenance;
6. accepts claim support only at token overlap `>= 0.35`;
7. selects the best verified answer, performs at most one repair, then returns
   no-answer;
8. degrades immediately to lexical/type/source-symbol retrieval while the
   embedding circuit is open.

## Evaluation

Corpus: 105 live repository questions across representative and boundary
categories, 12 verified-case problem/symptom/root-cause/solution questions,
and 8 intentionally absent no-answer questions.

| Metric | Pre-change | Deployed | Delta |
| --- | ---: | ---: | ---: |
| Recall@5 | 0.556 | 0.983 | +0.427 |
| Recall@10 | 0.624 | 1.000 | +0.376 |
| MRR | 0.535 | 0.887 | +0.352 |
| Failure-case Top-3 | 1.000 | 1.000 | 0 |
| No-answer accuracy | 0.500 | 1.000 | +0.500 |
| Citation validity | not separately measured | 1.000 | — |
| Expected file/line alignment | 0.767 | 0.991 | +0.224 |
| Unsupported-claim rejection | not connected | 1.000 | — |

The deterministic verifier execution also produced `1/1` for supported-claim
acceptance, unsupported-claim rejection, unknown-citation rejection,
single-repair success, and no-answer after a failed repair. Exactly one repair
callback was made.

### Live re-evaluation after source-freshness refresh (2026-07-28)

The source-freshness refresh exposed a flaw in the original repository
evaluation corpus: it assigned arbitrary source anchors to generic questions
and counted snapshots with no searchable knowledge as answerable retrieval
cases. The evaluator now counts a repository case in recall only when its
question terms map to a searchable, source-referenced knowledge item. Unsupported
categories remain explicit no-answer work and are not converted into false
retrieval misses or fabricated answers.

The deployed search also now includes component names and source file/symbol
references in lexical candidate generation and reward coverage. On 24
repository cases, 12 verified failure-case questions, and 8 no-answer questions,
the current live result is:

- Recall@5 and Recall@10: `0.889`
- MRR: `0.824`
- expected evidence alignment: `0.906`
- failure-case Top-3: `1.000`
- no-answer accuracy: `1.000`
- citation validity: `1.000`
- supported/unsupported/unknown-citation/repair/no-answer verifier checks: `1/1`

The historical 105-question result above remains the pre-refresh observation;
it is not directly comparable to this stricter answerability-labelled corpus.

Under active GPU contention, the first hybrid run took 23.4 seconds because
query embeddings timed out. Circuit-aware lexical/type fallback reduced the
same 125-case run to 3.40 seconds while producing the table above.

### Expected value before large-scale expansion

The measured `0.983` Recall@5 is an in-corpus result and must not be treated as
an unseen-project guarantee. For projects supported by the same static
analyzers and complete source hashes/line ranges, the pre-scale expectation is:

- Recall@5: `0.85–0.93`
- MRR: `0.70–0.85`
- failure-case Top-3: `>= 0.90`
- no-answer accuracy: `>= 0.95`
- citation validity: `>= 0.98`
- unsupported-claim rejection: `>= 0.95`

Expansion should stop if a held-out project falls below these bounds. Large
scale requires indexed symbol lookup/materialized candidate views and a
separate held-out, human-labelled evaluation set.

## Verification evidence

- Unit tests: `198 passed` after the final policy change.
- Isolated PostgreSQL integration: `34 passed`, temporary database dropped.
- OpenAPI SHA-256 remained identical for `SearchRequest`, `SearchResponse`,
  `SearchResult`, `Provenance`, and `RagRequest`.
- API image: `sha256:eadd4cb2f49bc85f6cdf5746d40d5609485c5a4ba3e01f912b635dc53a09ab67`.
- API readiness: PostgreSQL 18.4, schema `0017_repository_embeddings`, healthy.
- Rollback image:
  `local-knowledge-portal-app:rollback-pre-rag-20260727`
  (`sha256:34c4bb122ab1486a6ee95125feba36d397062342bd92c9e7a570709a4acd157f`).
- GPU recovery job `7dfa3226-0b98-4d53-a3a8-76dae3324ae3`: succeeded,
  examined 487, embedded 487, still deferred 0. A follow-up job
  `8c3ac46b-5e00-47a4-a20a-9e24a5edb2f4` embedded the 3 documents that arrived
  during the first run.
- Semantic chunks increased from 1,751 (`1.03%`) to 6,296 (`3.72%`). The low
  global percentage is expected because 6,134 active documents are
  `skipped_policy`; all 45 eligible portal documentation documents and all
  three verified-case documents are complete.
- Active/current embeddings: 6,271 at the configured revision and 0 mismatched
  revisions at the consistency check.
- Persistent semantic validation job
  `b7353717-f897-44fa-ad4f-15d8100a6714`: succeeded. Semantic and hybrid each
  returned three vector results, provenance was complete, best similarity was
  `0.999855`, and the snapshot state is `verified`.
- Standard validation: Ruff passed; non-integration tests 198 passed,
  1 skipped; isolated integration 34 passed; Compose config and PowerShell
  parse passed; the Docker web production build passed without redeploying UI.

## Reproduce

```bash
PYTHONPATH=services/api:services/indexer .venv/bin/python \
  scripts/evaluate_rag_quality.py \
  --base-url http://127.0.0.1:8010 --candidate-k 50 --workers 6

PYTHONPATH=services/api:services/indexer .venv/bin/pytest -q tests/unit
bash scripts/run-isolated-integration-tests.sh
```

## Rollback

No migration or UI rollback is required. Retag the preserved image as
`local-knowledge-portal-app:wsl` and recreate only the API service with
`docker compose ... up -d --no-deps api`. Search indexes and embeddings remain
derived data; source documents and repository snapshots are not modified.

## Unresolved and reproducibility

- The embedding runtime remains deliberately fail-closed as
  `deferred_gpu_recovery`; GPU-backed semantic queries are admitted by `gpuq`
  probes, while the live API uses the measured lexical/type/source-symbol
  fallback during shared-GPU contention.
- Continuous journal ingestion can create a small new deferred delta after a
  completed batch. The same idempotent GPUQ recovery path handles it; it does
  not invalidate the verified core-document/case coverage.
- The working tree contained extensive pre-existing modified and untracked
  repository-analysis work. The deployed image is reproducible from the exact
  current filesystem state and image ID, but not from a clean Git commit.
- Expected file/line evaluation is generated from current analysis snapshots.
  A held-out, manually adjudicated project set is required before distributed
  or multi-tenant expansion.
