# Retrieval baseline

Executed 2026-07-23 against a dedicated PostgreSQL test database and four-document Korean,
English, and code corpus. Semantic and hybrid modes used the real local Ollama
`qwen3-embedding:0.6b` production revision; the script refuses the production database.

| Mode | Hit Rate@5 | Hit Rate@10 | MRR | Filter correctness | Citation correctness |
|---|---:|---:|---:|---:|---:|
| Keyword | 0.6667 | 0.6667 | 0.6667 | 1.00 | 1.00 |
| Semantic | 1.00 | 1.00 | 0.8519 | 1.00 | 1.00 |
| Hybrid | 1.00 | 1.00 | 0.9259 | 1.00 | 1.00 |

No-answer correctness and stale-document exclusion both passed. The nine answerable cases cover
exact filename, partial path, Korean natural language, code symbol, exact error, work reason, ADR
decision, similar expression, and change time. Hybrid improves the lexical baseline without
hiding lexical/vector/fused score provenance.

Reproduce by setting `LKP_TEST_DATABASE_URL` to the dedicated test database and running
`uv run python tests/retrieval/evaluate.py`.
