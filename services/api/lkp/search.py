import hashlib
import time

from lkp_indexer.embedding import Embedder
from sqlalchemy import text
from sqlalchemy.orm import Session

from lkp.models import SearchQueryLog

from .schemas import Provenance, SearchRequest, SearchResponse, SearchResult
from .settings import Settings

INDEXED_LEXICAL_SQL = text(
    """
    WITH candidates AS MATERIALIZED (
      SELECT c.id chunk_id,
        ts_rank_cd(
          c.lexical_search_vector,
          websearch_to_tsquery('simple', :query)
        ) lexical_rank,
        0 path_match,
        0 symbol_match,
        0 fuzzy_match
      FROM document_chunk c
      WHERE :allow_text
        AND c.lexical_search_vector
          @@ websearch_to_tsquery('simple', :query)

      UNION ALL

      SELECT c.id chunk_id, 0 lexical_rank, 1 path_match, 0 symbol_match,
        0 fuzzy_match
      FROM document d
      JOIN document_version v ON v.id = d.current_version_id
      JOIN document_chunk c ON c.document_version_id = v.id
      WHERE :allow_path
        AND d.relative_path ILIKE :contains

      UNION ALL

      SELECT c.id chunk_id, 0 lexical_rank, 0 path_match, 1 symbol_match,
        0 fuzzy_match
      FROM document_chunk c
      WHERE :allow_symbol
        AND c.symbol_name IS NOT NULL
        AND lower(c.symbol_name) = lower(:query)
    ),
    ranked AS (
      SELECT chunk_id,
        max(lexical_rank) lexical_rank,
        max(path_match) path_match,
        max(symbol_match) symbol_match,
        max(fuzzy_match) fuzzy_match
      FROM candidates
      GROUP BY chunk_id
    )
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, d.project_key,
      ARRAY(SELECT t.name FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id ORDER BY t.name) tags,
      c.heading_path, c.symbol_name,
      c.start_line, c.end_line, c.content, c.content_hash, v.detected_at,
      ranked.lexical_rank, ranked.path_match, ranked.symbol_match,
      ranked.fuzzy_match
    FROM ranked
    JOIN document_chunk c ON c.id = ranked.chunk_id
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
      AND (CAST(:source_root_id AS uuid) IS NULL
           OR d.source_root_id = CAST(:source_root_id AS uuid))
      AND (CAST(:project AS text) IS NULL OR d.project_key = CAST(:project AS text))
      AND (
        cardinality(CAST(:tags AS text[])) = 0
        OR (
          CAST(:tag_mode AS text) = 'any'
          AND EXISTS (
            SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id AND t.name = ANY(CAST(:tags AS text[]))
          )
        )
        OR (
          CAST(:tag_mode AS text) = 'all'
          AND NOT EXISTS (
            SELECT 1 FROM unnest(CAST(:tags AS text[])) requested(name)
            WHERE NOT EXISTS (
              SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
              WHERE dt.document_id = d.id AND t.name = requested.name
            )
          )
        )
      )
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
    ORDER BY ranked.symbol_match DESC, ranked.path_match DESC,
      ranked.fuzzy_match DESC,
      ranked.lexical_rank DESC, d.relative_path, c.chunk_index
    LIMIT :limit
    """
)

FUZZY_FALLBACK_SQL = text(
    """
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, d.project_key,
      ARRAY(SELECT t.name FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id ORDER BY t.name) tags,
      c.heading_path, c.symbol_name,
      c.start_line, c.end_line, c.content, c.content_hash, v.detected_at,
      word_similarity(:query, c.content) lexical_rank,
      0 path_match, 0 symbol_match, 1 fuzzy_match
    FROM document_chunk c
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
      AND (CAST(:source_root_id AS uuid) IS NULL
           OR d.source_root_id = CAST(:source_root_id AS uuid))
      AND (CAST(:project AS text) IS NULL OR d.project_key = CAST(:project AS text))
      AND (
        cardinality(CAST(:tags AS text[])) = 0
        OR (CAST(:tag_mode AS text) = 'any' AND EXISTS (
          SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
          WHERE dt.document_id = d.id AND t.name = ANY(CAST(:tags AS text[]))
        ))
        OR (CAST(:tag_mode AS text) = 'all' AND NOT EXISTS (
          SELECT 1 FROM unnest(CAST(:tags AS text[])) requested(name)
          WHERE NOT EXISTS (
            SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id AND t.name = requested.name
          )
        ))
      )
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
      AND c.content %> :query
    ORDER BY word_similarity(:query, c.content) DESC
    LIMIT :limit
    """
)

CONTENT_FALLBACK_SQL = text(
    """
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, d.project_key,
      ARRAY(SELECT t.name FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id ORDER BY t.name) tags,
      c.heading_path, c.symbol_name,
      c.start_line, c.end_line, c.content, c.content_hash, v.detected_at,
      0 lexical_rank, 0 path_match, 0 symbol_match, 0 fuzzy_match
    FROM document_chunk c
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
      AND (CAST(:source_root_id AS uuid) IS NULL
           OR d.source_root_id = CAST(:source_root_id AS uuid))
      AND (CAST(:project AS text) IS NULL OR d.project_key = CAST(:project AS text))
      AND (
        cardinality(CAST(:tags AS text[])) = 0
        OR (CAST(:tag_mode AS text) = 'any' AND EXISTS (
          SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
          WHERE dt.document_id = d.id AND t.name = ANY(CAST(:tags AS text[]))
        ))
        OR (CAST(:tag_mode AS text) = 'all' AND NOT EXISTS (
          SELECT 1 FROM unnest(CAST(:tags AS text[])) requested(name)
          WHERE NOT EXISTS (
            SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id AND t.name = requested.name
          )
        ))
      )
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
      AND c.content ILIKE :contains
    ORDER BY d.relative_path, c.chunk_index
    LIMIT :limit
    """
)

VECTOR_SQL = text(
    """
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, d.filename, d.project_key,
      ARRAY(SELECT t.name FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id ORDER BY t.name) tags,
      c.heading_path, c.symbol_name,
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
      AND (
        cardinality(CAST(:tags AS text[])) = 0
        OR (CAST(:tag_mode AS text) = 'any' AND EXISTS (
          SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
          WHERE dt.document_id = d.id AND t.name = ANY(CAST(:tags AS text[]))
        ))
        OR (CAST(:tag_mode AS text) = 'all' AND NOT EXISTS (
          SELECT 1 FROM unnest(CAST(:tags AS text[])) requested(name)
          WHERE NOT EXISTS (
            SELECT 1 FROM document_tag dt JOIN tag t ON t.id = dt.tag_id
            WHERE dt.document_id = d.id AND t.name = requested.name
          )
        ))
      )
      AND (CAST(:path_prefix AS text) IS NULL
           OR lower(d.relative_path) LIKE lower(:path_filter))
    ORDER BY e.embedding <=> CAST(:vector AS vector)
    LIMIT :limit
    """
)


def classify_confidence(
    results: list[SearchResult],
    mode: str,
    *,
    high_similarity: float,
) -> str:
    if not results:
        return "none"
    if mode in {"keyword", "path", "symbol"}:
        return "high"
    if mode == "hybrid" and any((result.lexical_rank or 0) > 0 for result in results):
        return "high"
    best_similarity = max(
        (result.vector_similarity or -1 for result in results),
        default=-1,
    )
    return "high" if best_similarity >= high_similarity else "low"


def _params(request: SearchRequest) -> dict:
    return {
        "query": request.query,
        "contains": "%" + request.query + "%",
        "limit": request.top_k * 3,
        "allow_text": request.mode in {"keyword", "hybrid"},
        "allow_path": request.mode in {"keyword", "hybrid", "path"},
        "allow_symbol": request.mode in {"keyword", "hybrid", "symbol"},
        "source_root_id": str(request.source_root_id) if request.source_root_id else None,
        "project": request.project,
        "tags": sorted({item.strip().lower() for item in request.tags if item.strip()}),
        "tag_mode": request.tag_mode,
        "path_prefix": request.path_prefix,
        "path_filter": (request.path_prefix or "") + "%",
    }


def search(
    session: Session, request: SearchRequest, settings: Settings, embedder: Embedder | None = None
) -> SearchResponse:
    started = time.perf_counter()
    session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{settings.search_statement_timeout_ms}ms"},
    )
    lexical_rows = []
    vector_rows = []
    if request.mode in {"keyword", "hybrid", "path", "symbol"}:
        lexical_rows = list(session.execute(INDEXED_LEXICAL_SQL, _params(request)).mappings())
        if request.mode == "keyword" and not lexical_rows:
            session.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.15"))
            fallback_rows = list(session.execute(FUZZY_FALLBACK_SQL, _params(request)).mappings())
            indexed_chunk_ids = {row["chunk_id"] for row in lexical_rows}
            lexical_rows.extend(
                row for row in fallback_rows if row["chunk_id"] not in indexed_chunk_ids
            )
        if request.mode == "keyword" and not lexical_rows:
            fallback_rows = list(session.execute(CONTENT_FALLBACK_SQL, _params(request)).mappings())
            indexed_chunk_ids = {row["chunk_id"] for row in lexical_rows}
            lexical_rows.extend(
                row for row in fallback_rows if row["chunk_id"] not in indexed_chunk_ids
            )
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
        if row["symbol_match"]:
            item["why"].append("exact symbol match")
        if row["path_match"]:
            item["why"].append("path match")
        if row["fuzzy_match"]:
            item["why"].append("fuzzy content match")
        elif row["lexical_rank"]:
            item["why"].append("full-text match")
        if not item["why"]:
            item["why"].append("content fallback")
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
                project=row["project_key"],
                tags=list(row["tags"] or []),
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
    confidence = classify_confidence(
        results,
        request.mode,
        high_similarity=settings.semantic_high_confidence_similarity,
    )
    return SearchResponse(
        query=request.query,
        mode=request.mode,
        confidence=confidence,
        results=results,
        total=len(results),
    )
