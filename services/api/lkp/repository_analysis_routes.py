from __future__ import annotations

import hashlib
import posixpath
import uuid
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from lkp_indexer.embedding import CachedEmbedder, OllamaEmbedder
from lkp_indexer.embedding_runtime import timeout_circuit_state
from lkp_indexer.queue import enqueue
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import get_db
from .repository_rag_quality import infer_repository_types, repository_reward_rerank
from .settings import get_settings

router = APIRouter(prefix="/api/v1/repository-analysis", tags=["repository-analysis"])
settings = get_settings()


class RepositoryAnalysisRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="Library", min_length=1, max_length=100)


def _rows(db: Session, statement: str, params: dict | None = None) -> list[dict]:
    return [dict(item) for item in db.execute(text(statement), params or {}).mappings().all()]


def _normalize_source_path(value: str) -> str:
    normalized = posixpath.normpath(value.strip().replace("\\", "/"))
    if not normalized.startswith("/") or ".." in normalized.split("/"):
        raise HTTPException(422, "절대 경로를 입력해 주세요.")
    return normalized


@lru_cache(maxsize=1)
def _repository_query_embedder() -> CachedEmbedder:
    return CachedEmbedder(
        OllamaEmbedder(
            settings.ollama_base_url,
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dimension,
            settings.query_embedding_timeout_seconds,
            settings.embedding_keep_alive,
        ),
        max_entries=settings.query_embedding_cache_size,
        ttl_seconds=settings.query_embedding_cache_ttl_seconds,
    )


@router.get("/source-roots")
def list_repository_source_roots(db: Session = Depends(get_db)) -> dict:
    rows = _rows(
        db,
        """
        SELECT id, name, canonical_path, read_only
        FROM source_root
        WHERE enabled = true AND read_only = true
        ORDER BY length(canonical_path) DESC, name
        """,
    )
    return {"items": rows, "total": len(rows)}


@router.post("/analyze", status_code=202)
def request_repository_analysis(
    request: RepositoryAnalysisRequest,
    db: Session = Depends(get_db),
) -> dict:
    source_path = _normalize_source_path(request.source_path)
    source_root = db.execute(
        text(
            """
            SELECT id, name, canonical_path
            FROM source_root
            WHERE enabled = true AND read_only = true
              AND (
                :source_path = canonical_path OR
                :source_path LIKE canonical_path || '/%'
              )
            ORDER BY length(canonical_path) DESC
            LIMIT 1
            """
        ),
        {"source_path": source_path},
    ).mappings().one_or_none()
    if source_root is None:
        raise HTTPException(422, "허용된 읽기 전용 원본 경로 안의 폴더를 입력해 주세요.")

    request_id = uuid.uuid4()
    digest = hashlib.sha256(
        f"{source_root['id']}:{source_path}:{request_id}".encode()
    ).hexdigest()
    job = enqueue(
        db,
        key=f"repository-analysis:{digest}"[:128],
        source_root_id=source_root["id"],
        canonical_path=source_path,
        job_type="repository_analysis",
        max_attempts=3,
        priority=40,
        coalesce_pending=True,
        details={
            "request_id": str(request_id),
            "category": request.category,
            "source_root": source_root["name"],
            "requested_via": "repository_analysis_console",
        },
    )
    if job is None:
        raise HTTPException(409, "동일한 분석 요청이 이미 등록되어 있습니다.")
    db.commit()
    return {
        "job_id": job.id,
        "request_id": request_id,
        "status": job.status.value,
        "source_path": source_path,
        "source_root": source_root["name"],
        "max_attempts": job.max_attempts,
    }


@router.get("/analysis-requests/{job_id}")
def get_repository_analysis_request(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    row = db.execute(
        text(
            """
            SELECT id, canonical_path AS source_path, status, attempt_count,
                   max_attempts, leased_by, started_at, finished_at,
                   error_type, error_message, error_details, created_at, updated_at
            FROM ingest_job
            WHERE id = :job_id AND job_type = 'repository_analysis'
            """
        ),
        {"job_id": job_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(404, "분석 요청을 찾을 수 없습니다.")
    return dict(row)


@router.get("/projects")
def list_repository_projects(db: Session = Depends(get_db)) -> dict:
    projects = _rows(
        db,
        """
        SELECT p.id, p.canonical_name, p.display_name, p.category, p.status,
               p.updated_at, s.id AS snapshot_id, s.snapshot_name, s.source_hash,
               s.git_commit, s.git_branch, s.dirty_worktree, s.languages,
               s.build_systems, s.file_count, s.status AS analysis_status,
               s.created_at AS analyzed_at, s.stale,
               COALESCE(j.metrics, '{}'::jsonb) AS metrics,
               COALESCE(jsonb_array_length(j.warnings), 0) AS warning_count
        FROM repository_project p
        LEFT JOIN LATERAL (
          SELECT * FROM repository_snapshot rs
          WHERE rs.project_id = p.id
          ORDER BY rs.created_at DESC
          LIMIT 1
        ) s ON true
        LEFT JOIN LATERAL (
          SELECT metrics, warnings FROM repository_analysis_job raj
          WHERE raj.snapshot_id = s.id
          ORDER BY raj.started_at DESC
          LIMIT 1
        ) j ON true
        ORDER BY p.updated_at DESC, p.display_name
        """,
    )
    return {"items": projects, "total": len(projects)}


@router.get("/projects/{project_id}")
def get_repository_project(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    project = db.execute(
        text(
            """
            SELECT p.*, s.id AS snapshot_id, s.snapshot_name, s.source_hash,
                   s.git_commit, s.git_branch, s.dirty_worktree, s.languages,
                   s.build_systems, s.file_count, s.status AS analysis_status,
                   s.created_at AS analyzed_at
            FROM repository_project p
            LEFT JOIN LATERAL (
              SELECT * FROM repository_snapshot rs
              WHERE rs.project_id = p.id
              ORDER BY rs.created_at DESC LIMIT 1
            ) s ON true
            WHERE p.id = :project_id
            """
        ),
        {"project_id": project_id},
    ).mappings().one_or_none()
    if project is None:
        raise HTTPException(404, "repository project not found")
    snapshot_id = project["snapshot_id"]
    counts = {}
    relation_groups = []
    dependency_groups = []
    flow_items = []
    dependency_items = []
    components = []
    lifecycle_nodes = []
    lifecycle_edges = []
    dependency_usages = []
    if snapshot_id:
        counts = dict(
            db.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*) FROM repository_source_symbol
                       WHERE snapshot_id = :snapshot_id) AS symbols,
                      (SELECT count(*) FROM repository_source_relation
                       WHERE snapshot_id = :snapshot_id) AS relations,
                      (SELECT count(*) FROM repository_configuration_reference
                       WHERE snapshot_id = :snapshot_id) AS configurations,
                      (SELECT count(*) FROM repository_dependency_artifact
                       WHERE snapshot_id = :snapshot_id) AS dependencies,
                      (SELECT count(*) FROM repository_component
                       WHERE snapshot_id = :snapshot_id) AS components,
                      (SELECT count(*) FROM repository_lifecycle_node
                       WHERE snapshot_id = :snapshot_id) AS lifecycle_nodes,
                      (SELECT count(*) FROM repository_dependency_usage
                       WHERE snapshot_id = :snapshot_id) AS dependency_usages,
                      (SELECT count(*) FROM repository_knowledge_item
                       WHERE snapshot_id = :snapshot_id AND searchable) AS knowledge_items,
                      (SELECT count(*) FROM repository_knowledge_item
                       WHERE snapshot_id = :snapshot_id AND searchable
                         AND validation_status = 'SOURCE_VERIFIED') AS knowledge_source_verified,
                      (SELECT count(*) FROM repository_knowledge_item
                       WHERE snapshot_id = :snapshot_id AND searchable
                         AND validation_status = 'PARTIALLY_VERIFIED')
                       AS knowledge_partially_verified,
                      (SELECT count(*) FROM repository_knowledge_item
                       WHERE snapshot_id = :snapshot_id AND searchable
                         AND validation_status = 'ADDITIONAL_DATA_NEEDED')
                       AS knowledge_additional_data_needed,
                      (SELECT count(*) FROM repository_evaluation_result
                       WHERE snapshot_id = :snapshot_id) AS evaluation_results,
                      (SELECT count(*) FROM repository_evaluation_result
                       WHERE snapshot_id = :snapshot_id AND passed) AS evaluation_passed,
                      (SELECT count(*) FROM repository_evaluation_case c
                       JOIN repository_evaluation_result r ON r.case_id = c.id
                       WHERE r.snapshot_id = :snapshot_id
                         AND c.scenario_type IS NOT NULL) AS evaluation_scenarios
                    """
                ),
                {"snapshot_id": snapshot_id},
            ).mappings().one()
        )
        relation_groups = _rows(
            db,
            """
            SELECT relation_type AS key, count(*) AS count
            FROM repository_source_relation
            WHERE snapshot_id = :snapshot_id
            GROUP BY relation_type
            ORDER BY count(*) DESC, relation_type
            """,
            {"snapshot_id": snapshot_id},
        )
        dependency_groups = _rows(
            db,
            """
            SELECT artifact_type AS type, classification, count(*) AS count
            FROM repository_dependency_artifact
            WHERE snapshot_id = :snapshot_id
            GROUP BY artifact_type, classification
            ORDER BY count(*) DESC, artifact_type, classification
            """,
            {"snapshot_id": snapshot_id},
        )
        flow_items = _rows(
            db,
            """
            SELECT r.relative_path AS source_file,
                   target.relative_path AS target_file,
                   r.relation_type AS relation_type,
                   count(*) AS count
            FROM repository_source_relation r
            LEFT JOIN LATERAL (
              SELECT s.relative_path
              FROM repository_source_symbol s
              WHERE s.snapshot_id = r.snapshot_id
                AND s.symbol = r.target_symbol
              ORDER BY s.relative_path
              LIMIT 1
            ) target ON true
            WHERE r.snapshot_id = :snapshot_id
            GROUP BY r.relative_path, target.relative_path, r.relation_type
            ORDER BY count(*) DESC, r.relative_path, target.relative_path NULLS LAST
            LIMIT 40
            """,
            {"snapshot_id": snapshot_id},
        )
        dependency_items = _rows(
            db,
            """
            SELECT name, version, artifact_type, classification, relative_path,
                   scope, analysis_status
            FROM repository_dependency_artifact
            WHERE snapshot_id = :snapshot_id
            ORDER BY name, version NULLS LAST
            LIMIT 100
            """,
            {"snapshot_id": snapshot_id},
        )
        components = _rows(
            db,
            """
            SELECT component_key AS key, display_name, component_type,
                   responsibility, cardinality(relative_paths) AS file_count,
                   cardinality(entry_points) AS entry_point_count,
                   validation_status, confidence
            FROM repository_component
            WHERE snapshot_id = :snapshot_id
            ORDER BY
              CASE component_type
                WHEN '화면' THEN 1
                WHEN 'API' THEN 2
                WHEN '백그라운드 작업' THEN 3
                WHEN '데이터베이스' THEN 4
                ELSE 5
              END,
              display_name
            """,
            {"snapshot_id": snapshot_id},
        )
        lifecycle_nodes = _rows(
            db,
            """
            SELECT node_key AS key, phase, title, description, component_key,
                   sequence, validation_status, confidence
            FROM repository_lifecycle_node
            WHERE snapshot_id = :snapshot_id
            ORDER BY sequence
            """,
            {"snapshot_id": snapshot_id},
        )
        lifecycle_edges = _rows(
            db,
            """
            SELECT source_key, target_key, relation_type, label, provenance
            FROM repository_lifecycle_edge
            WHERE snapshot_id = :snapshot_id
            ORDER BY
              CASE provenance WHEN 'STATIC_CONFIRMED' THEN 0 ELSE 1 END,
              source_key, target_key
            """,
            {"snapshot_id": snapshot_id},
        )
        dependency_usages = _rows(
            db,
            """
            SELECT dependency_name, component_key, usage_type, provenance,
                   count(*) AS occurrence_count
            FROM repository_dependency_usage
            WHERE snapshot_id = :snapshot_id
            GROUP BY dependency_name, component_key, usage_type, provenance
            ORDER BY occurrence_count DESC, dependency_name, component_key
            """,
            {"snapshot_id": snapshot_id},
        )
    return {
        "project": dict(project),
        "counts": counts,
        "visualization": {
            "relation_groups": relation_groups,
            "dependency_groups": dependency_groups,
            "flow_items": flow_items,
            "dependency_items": dependency_items,
            "components": components,
            "lifecycle": {
                "nodes": lifecycle_nodes,
                "edges": lifecycle_edges,
            },
            "dependency_usages": dependency_usages,
        },
    }


@router.get("/projects/{project_id}/status")
def get_analysis_status(project_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rows = _rows(
        db,
        """
        SELECT j.id, j.correlation_id, j.snapshot_id, j.stage, j.status,
               j.analysis_version, j.model, j.model_quantization, j.prompt_version,
               j.metrics, j.warnings, j.error_code, j.error_message,
               j.started_at, j.finished_at
        FROM repository_analysis_job j
        WHERE j.project_id = :project_id
        ORDER BY j.started_at DESC
        LIMIT 25
        """,
        {"project_id": project_id},
    )
    return {"items": rows, "total": len(rows)}


@router.get("/projects/{project_id}/snapshots")
def list_repository_snapshots(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    rows = _rows(
        db,
        """
        SELECT id, snapshot_name, source_hash, git_commit, git_branch,
               dirty_worktree, analysis_base_time, file_count, languages,
               build_systems, status, stale, created_at
        FROM repository_snapshot
        WHERE project_id = :project_id
        ORDER BY created_at DESC
        """,
        {"project_id": project_id},
    )
    return {"items": rows, "total": len(rows)}


@router.get("/projects/{project_id}/evaluations")
def list_repository_evaluations(
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
) -> dict:
    rows = _rows(
        db,
        """
        SELECT c.id, c.question, c.question_type, c.expected_evidence,
               c.required_files, c.acceptable_answer, c.forbidden_assertions,
               c.grading_criteria, c.difficulty, c.scenario_type,
               r.snapshot_id, r.passed, r.score, r.failure_category,
               r.details, r.duration_ms, r.created_at
        FROM repository_evaluation_case c
        JOIN repository_evaluation_result r ON r.case_id = c.id
        JOIN repository_snapshot s ON s.id = r.snapshot_id
        WHERE c.project_id = :project_id
          AND (
            CAST(:snapshot_id AS uuid) IS NULL OR
            r.snapshot_id = CAST(:snapshot_id AS uuid)
          )
        ORDER BY r.created_at DESC, c.scenario_type NULLS FIRST, c.question
        LIMIT 200
        """,
        {"project_id": project_id, "snapshot_id": snapshot_id},
    )
    return {
        "items": rows,
        "total": len(rows),
        "scope": (
            "support_answer_quality"
            if any(
                bool((item.get("details") or {}).get("answer_quality_evaluated"))
                for item in rows
            )
            else "evidence_integrity"
        ),
        "answer_quality_evaluated": any(
            bool((item.get("details") or {}).get("answer_quality_evaluated"))
            for item in rows
        ),
    }


@router.get("/search")
def search_repository_knowledge(
    q: str = Query(min_length=2, max_length=500),
    project_id: uuid.UUID = Query(),
    snapshot_id: uuid.UUID = Query(),
    mode: Literal["keyword", "semantic", "hybrid"] = "hybrid",
    component: str | None = None,
    knowledge_type: str | None = None,
    validation_status: str | None = None,
    confidence: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    effective_mode = mode
    # COMPONENT is intentionally excluded from candidate expansion because it
    # is the broad fallback class; exact symbols/paths still enter lexically.
    preferred_types = sorted(infer_repository_types(q) - {"COMPONENT"})
    if mode in {"semantic", "hybrid"}:
        circuit = timeout_circuit_state(
            db,
            threshold=settings.embedding_timeout_circuit_threshold,
            window_seconds=settings.embedding_timeout_circuit_window_seconds,
            runtime_mode=settings.embedding_runtime_mode,
        )
        if circuit["open"]:
            effective_mode = "keyword"
    if effective_mode in {"semantic", "hybrid"}:
        try:
            query_vector = _repository_query_embedder().embed([q])[0]
        except Exception:
            effective_mode = "keyword"
            query_vector = [0.0] * settings.embedding_dimension
    else:
        query_vector = [0.0] * settings.embedding_dimension
    rows = _rows(
        db,
        """
        SELECT k.id, k.knowledge_type, k.title, k.summary, k.detail,
               k.processing_steps, k.components, k.configurations, k.dependencies,
               k.source_references, k.validation_status, k.confidence, k.unknowns,
               k.analysis_version, k.prompt_version,
               p.id AS project_id, p.display_name AS project,
               s.id AS snapshot_id, s.snapshot_name, s.source_hash,
               CASE
                 WHEN e.id IS NULL OR :mode = 'keyword' THEN NULL
                 ELSE 1 - (e.embedding <=> CAST(:query_vector AS vector))
               END AS vector_similarity,
               (
                 SELECT count(*)
                 FROM regexp_split_to_table(:query, '[[:space:]]+') AS matched_term
                 WHERE length(matched_term) >= 2
                   AND concat_ws(
                       ' ', k.title, k.summary, k.detail,
                       k.processing_steps::text,
                       array_to_string(k.components, ' '),
                       array_to_string(k.configurations, ' '),
                     array_to_string(k.dependencies, ' '),
                     k.source_references::text
                   ) ILIKE '%' || matched_term || '%'
               ) AS keyword_matches
        FROM repository_knowledge_item k
        JOIN repository_snapshot s ON s.id = k.snapshot_id
        JOIN repository_project p ON p.id = s.project_id
        LEFT JOIN repository_knowledge_embedding e
          ON e.knowledge_item_id = k.id
         AND e.embedding_revision = :embedding_revision
        WHERE k.searchable = true
          AND s.stale = false
          AND k.validation_status != 'REJECTED'
          AND p.id = CAST(:project_id AS uuid)
          AND s.id = CAST(:snapshot_id AS uuid)
          AND (
            CAST(:component AS text) IS NULL OR
            CAST(:component AS text) = ANY(k.components)
          )
          AND (
            CAST(:knowledge_type AS text) IS NULL OR
            k.knowledge_type = CAST(:knowledge_type AS text)
          )
          AND (
            CAST(:validation_status AS text) IS NULL OR
            k.validation_status = CAST(:validation_status AS text)
          )
          AND (
            CAST(:confidence AS text) IS NULL OR
            k.confidence = CAST(:confidence AS text)
          )
          AND (
            (
              :mode IN ('keyword', 'hybrid')
              AND EXISTS (
                SELECT 1
                FROM regexp_split_to_table(:query, '[[:space:]]+') AS term
                WHERE length(term) >= 2
                  AND concat_ws(
                      ' ', k.title, k.summary, k.detail,
                      k.processing_steps::text,
                      array_to_string(k.components, ' '),
                      array_to_string(k.configurations, ' '),
                    array_to_string(k.dependencies, ' '),
                    k.source_references::text
                  ) ILIKE '%' || term || '%'
              )
            )
            OR (:mode IN ('semantic', 'hybrid') AND e.id IS NOT NULL)
            OR (
              cardinality(CAST(:preferred_types AS text[])) > 0
              AND k.knowledge_type = ANY(CAST(:preferred_types AS text[]))
            )
          )
        ORDER BY
          CASE WHEN k.title ILIKE '%' || :query || '%' THEN 0 ELSE 1 END,
          CASE
            WHEN :mode = 'keyword' OR e.id IS NULL THEN NULL
            ELSE e.embedding <=> CAST(:query_vector AS vector)
          END,
          k.created_at DESC
        LIMIT :limit
        """,
        {
            "query": q,
            "mode": effective_mode,
            "query_vector": str(query_vector),
            "preferred_types": preferred_types,
            "embedding_revision": settings.embedding_revision,
            "project_id": project_id,
            "snapshot_id": snapshot_id,
            "component": component,
            "knowledge_type": knowledge_type,
            "validation_status": validation_status,
            "confidence": confidence,
            "limit": min(200, max(30, limit * 5)),
        },
    )
    symbol_rows = _rows(
        db,
        """
        SELECT sy.id, 'COMPONENT' AS knowledge_type, sy.symbol AS title,
               'Source declaration in the current repository snapshot.' AS summary,
               'Static declaration verified against the indexed source hash.' AS detail,
               ARRAY[]::text[] AS processing_steps,
               ARRAY[]::text[] AS components,
               ARRAY[]::text[] AS configurations,
               ARRAY[]::text[] AS dependencies,
               jsonb_build_array(
                 jsonb_build_object(
                   'file', sy.relative_path,
                   'symbol', sy.symbol,
                   'start_line', sy.start_line,
                   'end_line', sy.end_line,
                   'source_hash', sf.content_hash
                 )
               ) AS source_references,
               'SOURCE_VERIFIED' AS validation_status,
               'HIGH' AS confidence,
               ARRAY[]::text[] AS unknowns,
               'static-source-symbol-v1' AS analysis_version,
               'none' AS prompt_version,
               p.id AS project_id, p.display_name AS project,
               s.id AS snapshot_id, s.snapshot_name, s.source_hash,
               NULL::float AS vector_similarity,
               1::bigint AS keyword_matches
        FROM repository_source_symbol sy
        JOIN repository_source_file sf
          ON sf.snapshot_id = sy.snapshot_id
         AND sf.relative_path = sy.relative_path
        JOIN repository_snapshot s ON s.id = sy.snapshot_id
        JOIN repository_project p ON p.id = s.project_id
        WHERE p.id = CAST(:project_id AS uuid)
          AND s.id = CAST(:snapshot_id AS uuid)
          AND s.stale = false
          AND length(sy.symbol) >= 3
          AND :query ILIKE '%' || sy.symbol || '%'
        ORDER BY length(sy.symbol) DESC, sy.relative_path, sy.start_line
        LIMIT 30
        """,
        {
            "query": q,
            "project_id": project_id,
            "snapshot_id": snapshot_id,
        },
    )
    known_ids = {str(row["id"]) for row in rows}
    rows.extend(row for row in symbol_rows if str(row["id"]) not in known_ids)
    rows = repository_reward_rerank(rows, q, limit=limit)
    return {
        "items": rows,
        "total": len(rows),
        "mode": effective_mode,
        "requested_mode": mode,
        "embedding_revision": settings.embedding_revision,
        "latest_valid_snapshot_only": True,
    }


_TOOL_KNOWLEDGE_TYPES = {
    "get_project_architecture": ["ARCHITECTURE"],
    "get_component_detail": ["COMPONENT"],
    "get_logic_flow": ["LOGIC_FLOW"],
    "get_configuration_usage": ["CONFIGURATION"],
    "get_data_flow": ["DATA_FLOW"],
    "get_message_flow": ["MESSAGE_FLOW"],
    "get_dependency_usage": ["DEPENDENCY", "EMBEDDED_JAR"],
    "get_embedded_artifact_detail": ["EMBEDDED_JAR"],
    "get_troubleshooting_context": ["TROUBLESHOOTING", "ERROR_HANDLING", "RETRY_TIMEOUT"],
    "get_change_impact": ["CHANGE_IMPACT"],
}
ToolName = Literal[
    "get_project_architecture",
    "get_component_detail",
    "get_logic_flow",
    "get_configuration_usage",
    "get_data_flow",
    "get_message_flow",
    "get_dependency_usage",
    "get_embedded_artifact_detail",
    "get_troubleshooting_context",
    "get_change_impact",
]


@router.get("/tools/{tool_name}")
def repository_knowledge_tool(
    tool_name: ToolName,
    project_id: uuid.UUID,
    query: str = Query(default="", max_length=500),
    component: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    types = _TOOL_KNOWLEDGE_TYPES[tool_name]
    rows = _rows(
        db,
        """
        SELECT k.knowledge_type, k.title, k.summary, k.detail, k.processing_steps,
               k.components, k.configurations, k.dependencies, k.source_references,
               k.validation_status, k.confidence, k.unknowns,
               p.id AS project_id, p.display_name AS project,
               s.id AS snapshot_id, s.snapshot_name, s.source_hash
        FROM repository_knowledge_item k
        JOIN repository_snapshot s ON s.id = k.snapshot_id
        JOIN repository_project p ON p.id = s.project_id
        WHERE p.id = :project_id AND s.stale = false AND k.searchable = true
          AND k.knowledge_type = ANY(CAST(:types AS text[]))
          AND (
            CAST(:component AS text) IS NULL OR
            CAST(:component AS text) = ANY(k.components)
          )
          AND (
            :query = '' OR k.title ILIKE '%' || :query || '%' OR
            k.summary ILIKE '%' || :query || '%' OR
            k.detail ILIKE '%' || :query || '%'
          )
        ORDER BY k.created_at DESC
        LIMIT 50
        """,
        {
            "project_id": project_id,
            "types": types,
            "component": component,
            "query": query,
        },
    )
    return {
        "tool": tool_name,
        "context": rows,
        "grounding_required": True,
        "latest_valid_snapshot_only": True,
    }
