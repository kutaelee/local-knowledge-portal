# ADR 0003: PostgreSQL hybrid retrieval

Status: accepted

Keyword retrieval uses `tsvector`, GIN, trigram, filename/path matching, and phrase matching. Semantic retrieval uses pgvector cosine distance with an embedding-revision filter. Reciprocal Rank Fusion combines ranks and exposes lexical, vector, and fused scores with provenance.

ANN HNSW is a performance option, not a correctness dependency. Exact scan remains available for small or selective result sets. Korean-specific search infrastructure will be considered only after retrieval evaluation.
