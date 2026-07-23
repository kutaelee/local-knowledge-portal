import hashlib
import time

from lkp_indexer.embedding import Embedder
from sqlalchemy import text
from sqlalchemy.orm import Session

from lkp.models import SearchQueryLog

from .schemas import Provenance, SearchRequest, SearchResponse, SearchResult
from .settings import Settings

LEXICAL_SQL = text(
    """
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, c.heading_path, c.symbol_name,
      c.start_line, c.end_line, c.content, c.content_hash, v.detected_at,
      ts_rank_cd(c.lexical_search_vector, websearch_to_tsquery('simple', :query)) lexical_rank,
      CASE WHEN lower(d.relative_path) LIKE lower(:prefix) THEN 1 ELSE 0 END path_match
    FROM document_chunk c
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
      AND (CAST(:source_root_id AS uuid) IS NULL
           OR d.source_root_id = CAST(:source_root_id AS uuid))
      AND (CAST(:project AS text) IS NULL OR d.project_key = CAST(:project AS text))
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
      AND (
        c.lexical_search_vector @@ websearch_to_tsquery('simple', :query)
        OR lower(c.content) LIKE lower(:contains)
        OR lower(d.relative_path) LIKE lower(:contains)
        OR similarity(d.relative_path, :query) > 0.15
        OR lower(coalesce(c.symbol_name, '')) = lower(:query)
      )
    ORDER BY path_match DESC, lexical_rank DESC, similarity(d.relative_path, :query) DESC
    LIMIT :limit
    """
)

VECTOR_SQL = text(
    """
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, c.heading_path, c.symbol_name,
      c.start_line, c.end_line, c.content, c.content_hash, v.detected_at,
      1 - (e.embedding <=> CAST(:vector AS vector)) vector_similarity
    FROM chunk_embedding e
    JOIN document_chunk c ON c.id = e.chunk_id
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
      AND e.embedding_revision = :revision
      AND (CAST(:source_root_id AS uuid) IS NULL
           OR d.source_root_id = CAST(:source_root_id AS uuid))
      AND (CAST(:project AS text) IS NULL OR d.project_key = CAST(:project AS text))
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
    ORDER BY e.embedding <=> CAST(:vector AS vector)
    LIMIT :limit
    """
)


def _params(request: SearchRequest) -> dict:
    return {
        "query": request.query,
        "prefix": request.query + "%",
        "contains": "%" + request.query + "%",
        "limit": request.top_k * 3,
        "source_root_id": str(request.source_root_id) if request.source_root_id else None,
        "project": request.project,
        "path_prefix": request.path_prefix,
        "path_filter": (request.path_prefix or "") + "%",
    }


def search(
    session: Session, request: SearchRequest, settings: Settings, embedder: Embedder | None = None
) -> SearchResponse:
    started = time.perf_counter()
    lexical_rows = []
    vector_rows = []
    if request.mode in {"keyword", "hybrid", "path", "symbol"}:
        lexical_rows = list(session.execute(LEXICAL_SQL, _params(request)).mappings())
    if request.mode in {"semantic", "hybrid"} and embedder is not None:
        vector = embedder.embed([request.query])[0]
        vector_rows = list(
            session.execute(
                VECTOR_SQL,
                {
                    **_params(request),
                    "vector": str(vector),
                    "revision": request.embedding_revision or settings.embedding_revision,
                },
            ).mappings()
        )
    scores: dict[str, dict] = {}
    rrf_k = 60
    for rank, row in enumerate(lexical_rows, 1):
        item = scores.setdefault(
            str(row["chunk_id"]), {"row": row, "fused": 0, "lex": None, "vec": None, "why": []}
        )
        item["fused"] += 1 / (rrf_k + rank)
        item["lex"] = float(row["lexical_rank"] or 0)
        item["why"].append("keyword/path match")
    for rank, row in enumerate(vector_rows, 1):
        similarity = float(row["vector_similarity"])
        if similarity < request.minimum_similarity:
            continue
        item = scores.setdefault(
            str(row["chunk_id"]), {"row": row, "fused": 0, "lex": None, "vec": None, "why": []}
        )
        item["fused"] += 1 / (rrf_k + rank)
        item["vec"] = similarity
        item["why"].append("semantic similarity")
    ordered = sorted(scores.values(), key=lambda item: item["fused"], reverse=True)[: request.top_k]
    results = []
    for item in ordered:
        row = item["row"]
        results.append(
            SearchResult(
                title=row["filename"],
                heading_or_symbol=row["heading_path"] or row["symbol_name"],
                snippet=row["content"][:800],
                lexical_rank=item["lex"],
                vector_similarity=item["vec"],
                fused_rank=item["fused"],
                match_reason=item["why"],
                provenance=Provenance(
                    document_id=row["document_id"],
                    document_version_id=row["version_id"],
                    chunk_id=row["chunk_id"],
                    source_root=row["source_root"],
                    canonical_path=row["canonical_path"],
                    relative_path=row["relative_path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    content_hash=row["content_hash"],
                    indexed_timestamp=row["detected_at"].isoformat(),
                ),
            )
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    session.add(
        SearchQueryLog(
            query_hash=hashlib.sha256(request.query.encode()).hexdigest(),
            mode=request.mode,
            result_count=len(results),
            duration_ms=elapsed,
        )
    )
    best = max((item.fused_rank for item in results), default=0)
    confidence = "none" if not results else ("high" if best >= 1 / 61 else "low")
    return SearchResponse(
        query=request.query,
        mode=request.mode,
        confidence=confidence,
        results=results,
        total=len(results),
    )
