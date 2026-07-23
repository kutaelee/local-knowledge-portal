# Retrieval baseline

Executed 2026-07-23 against one bilingual fixture document, using keyword mode only. Semantic quality was not evaluated because Ollama and the production model were unavailable.

| Metric | Baseline |
|---|---:|
| Hit Rate@5 | 0.75 |
| Hit Rate@10 | 0.75 |
| MRR | 0.75 |
| no-answer correctness | 1.00 |
| citation structural correctness | 1.00 |

Six of eight answerable queries retrieved the fixture at rank 1. The simple PostgreSQL configuration missed a Korean natural-language query and a similar-expression query. Both expected no-answer cases returned no results. Filename, partial path, exact phrase, lease error, work reason, and ADR decision queries succeeded.

This is a baseline, not a release target. It demonstrates the expected weakness of simple lexical search and provides a non-regression reference for a later Ollama-backed hybrid evaluation. Citation correctness here means a canonical path, positive ordered line range, and 64-character content hash were present; it does not claim human semantic verification beyond the fixture.

Reproduce with `uv run python tests/retrieval/evaluate.py`.
