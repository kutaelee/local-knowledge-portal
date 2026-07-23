# System architecture

```mermaid
flowchart LR
  FS[Read-only repositories and Markdown] --> SCAN[Scanner]
  FS --> WATCH[watchfiles watcher]
  RECON[Periodic reconciliation] --> JOBS[(PostgreSQL queue)]
  SCAN --> JOBS
  WATCH --> JOBS
  JOBS --> WORKER[Leased worker]
  WORKER --> PARSE[Deterministic parser and chunker]
  PARSE --> EMBED[Ollama embedding adapter]
  EMBED --> DB[(PostgreSQL 18 + pgvector)]
  DB --> API[FastAPI]
  API --> WEB[Next.js portal]
  API --> RAG[RAG REST context]
  DB --> BACKUP[Custom-format backup]
  BACKUP --> D[D drive immutable directory]
```

The PostgreSQL queue uses `FOR UPDATE SKIP LOCKED`, lease expiry, bounded retry, dead-letter status, idempotency keys, and per-document advisory locks. A watcher only enqueues work. Periodic reconciliation is the correctness backstop.

Windows paths are resolved before use, compared with `os.path.commonpath`, stored using canonical Windows separators, and compared case-insensitively for idempotency. Reparse points are not traversed. `(source_root_id, canonical_path)` is unique.

Markdown chunks preserve frontmatter, heading hierarchy, source lines, and content hashes. Code parsing currently provides deterministic symbol-pattern chunks and a line-window fallback; tree-sitter is the planned parser adapter for broader grammar-specific extraction.

Lexical retrieval combines simple `tsvector`, GIN, trigram, and path/symbol conditions. Semantic retrieval filters by explicit embedding revision and uses cosine distance. Hybrid retrieval uses Reciprocal Rank Fusion and exposes component scores.
