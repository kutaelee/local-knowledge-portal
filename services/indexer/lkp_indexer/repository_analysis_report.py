"""Generate an evidence-backed IndigoESB repository-analysis report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from sqlalchemy import text

from .repository_reference_policy import latest_snapshot_sql

_LATEST_REPOSITORY_SNAPSHOT = latest_snapshot_sql("s")

_REQUIRED_SUPPORT_CATEGORIES = (
    "ENTRY_POINT",
    "CALL_FLOW",
    "IMPLEMENTATION_SELECTION",
    "CONFIG_PRIORITY",
    "EXCEPTION_CONDITION",
    "RETRY_TIMEOUT",
    "TRANSACTION",
    "DB_MESSAGE_FLOW",
    "CONCURRENCY_STATE",
    "DEPENDENCY_USAGE",
    "JAR_MISSING_IMPACT",
    "LOG_LOCATION",
    "FAILURE_CANDIDATES",
    "ADDITIONAL_EVIDENCE",
    "CHANGE_IMPACT",
)
_REQUIRED_PROJECT_CATEGORIES = (
    "IndigoESB esb",
    "IndigoESB imc",
    "IndigoESB agent",
)
_REQUIRED_EVALUATION_SCOPES = (
    "post_persistence_hybrid_retrieval",
    "post_persistence_support_answer",
)


def _rows(session: Any, statement: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in session.execute(text(statement), params or {}).mappings().all()]


def collect(session_factory: Any = SessionLocal) -> dict[str, Any]:
    with session_factory() as session:
        projects = _rows(
            session,
            f"""
            SELECT p.id AS project_id, p.display_name, p.category,
                   s.id AS snapshot_id, s.source_hash, s.snapshot_name,
                   s.file_count, s.status AS snapshot_status, s.created_at,
                   j.id AS job_id, j.correlation_id, j.model,
                   j.model_quantization, j.prompt_version, j.metrics, j.warnings
            FROM repository_project p
            JOIN repository_snapshot s ON s.project_id = p.id
            LEFT JOIN LATERAL (
              SELECT * FROM repository_analysis_job x
              WHERE x.snapshot_id = s.id
              ORDER BY x.started_at DESC LIMIT 1
            ) j ON true
            WHERE p.category LIKE 'IndigoESB %'
              AND {_LATEST_REPOSITORY_SNAPSHOT}
            ORDER BY p.display_name
            """,
        )
        for project in projects:
            snapshot_id = project["snapshot_id"]
            project["counts"] = dict(
                session.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM repository_source_file
                           WHERE snapshot_id=:s) AS files,
                          (
                            SELECT count(DISTINCT covered_file)
                            FROM repository_analysis_task t
                            JOIN repository_analysis_job j ON j.id=t.job_id
                            CROSS JOIN LATERAL jsonb_array_elements_text(
                              COALESCE(t.scope->'source_files', '[]'::jsonb)
                            ) AS files(covered_file)
                            WHERE j.snapshot_id=:s AND t.attempt_count > 0
                          ) AS qwen_analyzed_files,
                          (SELECT count(*) FROM repository_source_symbol
                           WHERE snapshot_id=:s) AS symbols,
                          (SELECT count(*) FROM repository_source_relation
                           WHERE snapshot_id=:s) AS relations,
                          (SELECT count(*) FROM repository_configuration_reference
                           WHERE snapshot_id=:s) AS configurations,
                          (SELECT count(*) FROM repository_dependency_artifact
                           WHERE snapshot_id=:s) AS dependencies,
                          (SELECT count(*) FROM repository_dependency_usage
                           WHERE snapshot_id=:s) AS dependency_usages,
                          (SELECT count(*) FROM repository_component
                           WHERE snapshot_id=:s) AS components,
                          (SELECT count(*) FROM repository_lifecycle_node
                           WHERE snapshot_id=:s) AS lifecycle_nodes,
                          (SELECT count(*) FROM repository_analysis_claim c
                           JOIN repository_analysis_job j ON j.id=c.job_id
                           WHERE j.snapshot_id=:s) AS claims,
                          (SELECT count(*) FROM repository_analysis_claim c
                           JOIN repository_analysis_job j ON j.id=c.job_id
                           WHERE j.snapshot_id=:s
                             AND c.validation_status='SOURCE_VERIFIED')
                            AS verified_claims,
                          (SELECT count(*) FROM repository_knowledge_item
                           WHERE snapshot_id=:s) AS knowledge_items,
                          (SELECT count(*) FROM repository_knowledge_item
                           WHERE snapshot_id=:s AND searchable)
                            AS searchable_knowledge,
                          (SELECT count(*) FROM repository_knowledge_embedding e
                           JOIN repository_knowledge_item k
                             ON k.id=e.knowledge_item_id
                           WHERE k.snapshot_id=:s) AS embeddings
                        """
                    ),
                    {"s": snapshot_id},
                )
                .mappings()
                .one()
            )
            project["task_statuses"] = _rows(
                session,
                """
                SELECT t.status, t.failure_code, count(*) AS count,
                       sum(t.attempt_count) AS attempts
                FROM repository_analysis_task t
                JOIN repository_analysis_job j ON j.id=t.job_id
                WHERE j.snapshot_id=:s
                GROUP BY t.status, t.failure_code
                ORDER BY t.status, t.failure_code
                """,
                {"s": snapshot_id},
            )
            project["task_details"] = _rows(
                session,
                """
                SELECT t.task_type, t.scope->>'analysis_unit' AS analysis_unit,
                       t.status, t.attempt_count, t.failure_code,
                       COALESCE(
                         (t.scope->>'codex_intervened')::boolean, false
                       ) AS codex_intervened,
                       COALESCE(
                         (t.scope->>'codex_claims_authored')::int, 0
                       ) AS codex_claims_authored,
                       COALESCE(
                         t.scope->'codex_source_scope', '[]'::jsonb
                       ) AS codex_source_scope,
                       COALESCE(
                         t.scope->'source_files', '[]'::jsonb
                       ) AS source_files,
                       COALESCE(
                         t.scope->'missing_knowledge', '[]'::jsonb
                       ) AS missing_knowledge,
                       COALESCE(
                         t.scope->'contradictions', '[]'::jsonb
                       ) AS contradictions,
                       COALESCE(
                         t.scope->'failure_history', '[]'::jsonb
                       ) AS failure_history,
                       COALESCE(
                         t.scope->'retry_history', '[]'::jsonb
                       ) AS retry_history,
                       COALESCE(
                         t.scope->'additional_evidence_requests', '[]'::jsonb
                       ) AS additional_evidence_requests,
                       t.started_at, t.finished_at
                FROM repository_analysis_task t
                JOIN repository_analysis_job j ON j.id=t.job_id
                WHERE j.snapshot_id=:s
                ORDER BY t.started_at NULLS LAST, t.task_type, analysis_unit
                """,
                {"s": snapshot_id},
            )
            project["claim_statuses"] = _rows(
                session,
                """
                SELECT c.validation_status, c.claim_type, count(*) AS count
                FROM repository_analysis_claim c
                JOIN repository_analysis_job j ON j.id=c.job_id
                WHERE j.snapshot_id=:s
                GROUP BY c.validation_status, c.claim_type
                ORDER BY c.validation_status, c.claim_type
                """,
                {"s": snapshot_id},
            )
            project["verified_claims"] = _rows(
                session,
                """
                SELECT c.claim_text, c.claim_type, c.component, c.evidence,
                       c.related_configs, c.assumptions, c.unknowns,
                       c.counter_evidence, c.confidence
                FROM repository_analysis_claim c
                JOIN repository_analysis_job j ON j.id=c.job_id
                WHERE j.snapshot_id=:s
                  AND c.validation_status='SOURCE_VERIFIED'
                ORDER BY c.component, c.claim_type, c.claim_text
                """,
                {"s": snapshot_id},
            )
            project["knowledge"] = _rows(
                session,
                """
                SELECT knowledge_type, title, summary, processing_steps,
                       components, configurations, dependencies,
                       source_references, confidence, unknowns, searchable
                FROM repository_knowledge_item
                WHERE snapshot_id=:s
                ORDER BY searchable DESC, knowledge_type, title
                """,
                {"s": snapshot_id},
            )
            project["evaluation_scopes"] = _rows(
                session,
                """
                SELECT COALESCE(r.details->>'scope', 'analysis_evaluation') AS scope,
                       count(*) AS count,
                       count(*) FILTER (WHERE r.passed) AS passed,
                       avg(r.score) AS average_score
                FROM repository_evaluation_result r
                WHERE r.snapshot_id=:s
                GROUP BY COALESCE(r.details->>'scope', 'analysis_evaluation')
                ORDER BY scope
                """,
                {"s": snapshot_id},
            )
            project["evaluation_categories"] = _rows(
                session,
                """
                SELECT c.question_type,
                       count(*) AS result_count,
                       count(*) FILTER (WHERE r.passed) AS passed,
                       array_agg(
                         DISTINCT COALESCE(
                           r.details->>'scope', 'analysis_evaluation'
                         )
                       ) AS scopes
                FROM repository_evaluation_case c
                JOIN repository_evaluation_result r ON r.case_id=c.id
                WHERE c.project_id=:p AND r.snapshot_id=:s
                GROUP BY c.question_type
                ORDER BY c.question_type
                """,
                {"p": project["project_id"], "s": snapshot_id},
            )
            project["evaluation_failures"] = _rows(
                session,
                """
                SELECT failure_category, count(*) AS count
                FROM repository_evaluation_result
                WHERE snapshot_id=:s AND passed=false
                GROUP BY failure_category
                ORDER BY failure_category
                """,
                {"s": snapshot_id},
            )

        invariants = dict(
            session.execute(
                text(
                    """
                    WITH indigo_snapshots AS (
                      SELECT s.id
                      FROM repository_snapshot s
                      JOIN repository_project p ON p.id=s.project_id
                      WHERE p.category LIKE 'IndigoESB %'
                    ),
                    invalid_claim_refs AS (
                      SELECT count(*) AS value
                      FROM repository_analysis_claim c
                      JOIN repository_analysis_job j ON j.id=c.job_id
                      JOIN indigo_snapshots i ON i.id=j.snapshot_id
                      CROSS JOIN LATERAL jsonb_array_elements(c.evidence) e
                      WHERE NOT EXISTS (
                        SELECT 1 FROM repository_source_file f
                        WHERE f.snapshot_id=j.snapshot_id
                          AND f.relative_path=e->>'file'
                          AND f.content_hash=e->>'source_hash'
                          AND (e->>'start_line')::int >= 1
                          AND (e->>'end_line')::int >= (e->>'start_line')::int
                          AND (e->>'end_line')::int <= f.line_count
                      )
                    ),
                    invalid_knowledge_refs AS (
                      SELECT count(*) AS value
                      FROM repository_knowledge_item k
                      JOIN indigo_snapshots i ON i.id=k.snapshot_id
                      CROSS JOIN LATERAL jsonb_array_elements(k.source_references) e
                      WHERE NOT EXISTS (
                        SELECT 1 FROM repository_source_file f
                        WHERE f.snapshot_id=k.snapshot_id
                          AND f.relative_path=e->>'file'
                          AND f.content_hash=e->>'source_hash'
                          AND (e->>'start_line')::int >= 1
                          AND (e->>'end_line')::int >= (e->>'start_line')::int
                          AND (e->>'end_line')::int <= f.line_count
                      )
                    ),
                    forbidden_source_payloads AS (
                      SELECT count(*) AS value
                      FROM (
                        SELECT concat_ws(
                          ' ', j.metrics::text, j.warnings::text
                        ) AS payload
                        FROM repository_analysis_job j
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                        UNION ALL
                        SELECT t.scope::text
                        FROM repository_analysis_task t
                        JOIN repository_analysis_job j ON j.id=t.job_id
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                        UNION ALL
                        SELECT concat_ws(
                          ' ',
                          c.evidence::text,
                          c.related_configs::text,
                          c.assumptions::text,
                          c.unknowns::text,
                          c.counter_evidence::text,
                          c.validation_errors::text
                        )
                        FROM repository_analysis_claim c
                        JOIN repository_analysis_job j ON j.id=c.job_id
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                        UNION ALL
                        SELECT concat_ws(
                          ' ',
                          k.processing_steps::text,
                          k.source_references::text,
                          k.unknowns::text
                        )
                        FROM repository_knowledge_item k
                        JOIN indigo_snapshots i ON i.id=k.snapshot_id
                        UNION ALL
                        SELECT c.evidence::text
                        FROM repository_component c
                        JOIN indigo_snapshots i ON i.id=c.snapshot_id
                        UNION ALL
                        SELECT n.evidence::text
                        FROM repository_lifecycle_node n
                        JOIN indigo_snapshots i ON i.id=n.snapshot_id
                        UNION ALL
                        SELECT e.evidence::text
                        FROM repository_lifecycle_edge e
                        JOIN indigo_snapshots i ON i.id=e.snapshot_id
                        UNION ALL
                        SELECT concat_ws(
                          ' ',
                          c.expected_evidence::text,
                          c.forbidden_assertions::text,
                          c.grading_criteria::text
                        )
                        FROM repository_evaluation_case c
                        JOIN repository_project p ON p.id=c.project_id
                        WHERE p.category LIKE 'IndigoESB %'
                        UNION ALL
                        SELECT r.details::text
                        FROM repository_evaluation_result r
                        JOIN indigo_snapshots i ON i.id=r.snapshot_id
                      ) payloads
                      WHERE payload ~
                        '"(source_text|raw_source|decompiled_source|source_excerpt|source_content|prompt|prompt_text|request_body|response_body)"[[:space:]]*:'
                    )
                    SELECT
                      (SELECT value FROM invalid_claim_refs) AS invalid_claim_refs,
                      (SELECT value FROM invalid_knowledge_refs)
                        AS invalid_knowledge_refs,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_item k
                        JOIN repository_snapshot s ON s.id=k.snapshot_id
                        JOIN repository_project p ON p.id=s.project_id
                        WHERE p.category LIKE 'IndigoESB %'
                          AND s.stale=true AND k.searchable=true
                      ) AS searchable_stale_knowledge,
                      (
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_schema=current_schema()
                        AND table_name LIKE 'repository\\_%' ESCAPE '\\'
                        AND column_name IN (
                          'content',
                          'source_text',
                          'raw_source',
                          'decompiled_source',
                          'source_excerpt',
                          'source_content',
                          'prompt',
                          'prompt_text',
                          'request_body',
                          'response_body'
                        )
                      ) AS raw_source_columns,
                      (SELECT value FROM forbidden_source_payloads)
                        AS forbidden_source_payloads
                      ,
                      (
                        SELECT count(*)
                        FROM repository_analysis_job j
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                        WHERE j.model IS NULL
                           OR lower(j.model) NOT LIKE 'qwen3.6%'
                      ) AS non_qwen_analysis_jobs,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_item k
                        JOIN indigo_snapshots i ON i.id=k.snapshot_id
                        WHERE k.searchable=true
                          AND k.validation_status NOT IN (
                            'SOURCE_VERIFIED',
                            'PARTIALLY_VERIFIED',
                            'TEST_VERIFIED',
                            'RUNTIME_VERIFIED',
                            'HUMAN_APPROVED',
                            'ADDITIONAL_DATA_NEEDED'
                          )
                      ) AS searchable_ineligible_statuses,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_embedding e
                        JOIN repository_knowledge_item k
                          ON k.id=e.knowledge_item_id
                        JOIN repository_snapshot s ON s.id=k.snapshot_id
                        JOIN repository_project p ON p.id=s.project_id
                        WHERE p.category LIKE 'IndigoESB %'
                          AND s.stale=true
                      ) AS stale_snapshot_embeddings,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_embedding e
                        JOIN repository_knowledge_item k
                          ON k.id=e.knowledge_item_id
                        JOIN indigo_snapshots i ON i.id=k.snapshot_id
                        WHERE k.searchable=false
                      ) AS nonsearchable_knowledge_embeddings,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_embedding e
                        JOIN repository_knowledge_item k
                          ON k.id=e.knowledge_item_id
                        JOIN indigo_snapshots i ON i.id=k.snapshot_id
                        WHERE length(e.embedding_text_hash) <> 64
                           OR btrim(e.embedding_revision) = ''
                           OR btrim(e.provider) = ''
                           OR btrim(e.model) = ''
                           OR btrim(e.model_digest) = ''
                           OR e.dimension <> 1024
                      ) AS invalid_embedding_metadata,
                      (
                        SELECT count(*)
                        FROM repository_knowledge_item k
                        JOIN indigo_snapshots i ON i.id=k.snapshot_id
                        WHERE k.searchable=true
                          AND NOT EXISTS (
                            SELECT 1
                            FROM repository_knowledge_embedding e
                            WHERE e.knowledge_item_id=k.id
                          )
                      ) AS searchable_knowledge_without_embedding,
                      (
                        SELECT COALESCE(
                          sum(
                            COALESCE(
                              (t.scope->>'codex_claims_authored')::int, 0
                            )
                          ), 0
                        )
                        FROM repository_analysis_task t
                        JOIN repository_analysis_job j ON j.id=t.job_id
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                      ) AS codex_authored_claims
                      ,
                      (
                        SELECT count(*)
                        FROM repository_source_file f
                        JOIN indigo_snapshots i ON i.id=f.snapshot_id
                        WHERE NOT EXISTS (
                          SELECT 1
                          FROM repository_analysis_task t
                          JOIN repository_analysis_job j ON j.id=t.job_id
                          CROSS JOIN LATERAL jsonb_array_elements_text(
                            COALESCE(t.scope->'source_files', '[]'::jsonb)
                          ) AS files(covered_file)
                          WHERE j.snapshot_id=f.snapshot_id
                            AND t.attempt_count > 0
                            AND covered_file=f.relative_path
                        )
                      ) AS qwen_unanalyzed_source_files,
                      (
                        SELECT count(*)
                        FROM repository_analysis_task t
                        JOIN repository_analysis_job j ON j.id=t.job_id
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                        WHERE t.attempt_count=0
                      ) AS unexecuted_analysis_tasks
                      ,
                      (
                        SELECT COALESCE(
                          sum(
                            COALESCE(
                              (j.metrics->>'unresolved_skipped_file_count')::int,
                              0
                            )
                          ), 0
                        )
                        FROM repository_analysis_job j
                        JOIN indigo_snapshots i ON i.id=j.snapshot_id
                      ) AS unresolved_source_files
                    """
                )
            )
            .mappings()
            .one()
        )
        present_projects = {project["category"] for project in projects}
        missing_projects = sorted(set(_REQUIRED_PROJECT_CATEGORIES) - present_projects)
        missing_categories: dict[str, list[str]] = {}
        missing_scopes: dict[str, dict[str, list[str]]] = {}
        missing_scope_totals = {
            scope: len(missing_projects) * len(_REQUIRED_SUPPORT_CATEGORIES)
            for scope in _REQUIRED_EVALUATION_SCOPES
        }
        for project in projects:
            category_scopes = {
                row["question_type"]: set(row["scopes"] or [])
                for row in project["evaluation_categories"]
            }
            project_missing_scopes: dict[str, list[str]] = {}
            for category in _REQUIRED_SUPPORT_CATEGORIES:
                missing = sorted(
                    set(_REQUIRED_EVALUATION_SCOPES) - category_scopes.get(category, set())
                )
                if missing:
                    project_missing_scopes[category] = missing
                    for scope in missing:
                        missing_scope_totals[scope] += 1
            missing_scopes[project["category"]] = project_missing_scopes
            missing_categories[project["category"]] = sorted(project_missing_scopes)
        invariants["missing_required_projects"] = len(missing_projects)
        invariants["missing_support_categories"] = sum(
            len(items) for items in missing_categories.values()
        ) + len(missing_projects) * len(_REQUIRED_SUPPORT_CATEGORIES)
        invariants["missing_post_persistence_retrieval_categories"] = missing_scope_totals[
            "post_persistence_hybrid_retrieval"
        ]
        invariants["missing_post_persistence_answer_categories"] = missing_scope_totals[
            "post_persistence_support_answer"
        ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
        "invariants": invariants,
        "completeness": {
            "required_projects": list(_REQUIRED_PROJECT_CATEGORIES),
            "missing_projects": missing_projects,
            "required_support_categories": list(_REQUIRED_SUPPORT_CATEGORIES),
            "required_evaluation_scopes": list(_REQUIRED_EVALUATION_SCOPES),
            "missing_support_categories": missing_categories,
            "missing_support_scopes": missing_scopes,
        },
    }


def _line(label: str, value: Any) -> str:
    return f"- {label}: {value}"


def _json_inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _evidence_text(references: Any) -> str:
    rendered = []
    for reference in references or []:
        rendered.append(
            f"{reference.get('file')}:{reference.get('start_line')}-"
            f"{reference.get('end_line')}"
            + (f" ({reference.get('symbol')})" if reference.get("symbol") else "")
        )
    return ", ".join(rendered) if rendered else "인용 없음"


def render_markdown(report: dict[str, Any]) -> str:
    projects = report["projects"]
    lines = [
        "# IndigoESB Qwen3.6 레포 분석 검증 보고서",
        "",
        _line("생성 시각", report["generated_at"]),
        _line("대상 프로젝트 수", len(projects)),
        "",
        "## 1. Qwen이 분석한 내용",
        "",
    ]
    for project in projects:
        counts = project["counts"]
        lines.extend(
            [
                f"### {project['display_name']}",
                "",
                _line("모델", project.get("model")),
                _line("양자화", project.get("model_quantization")),
                _line("Snapshot", project["snapshot_id"]),
                _line("source hash", project["source_hash"]),
                _line(
                    "Qwen 분석 파일",
                    f"{counts.get('qwen_analyzed_files', 0)}/{counts['files']}",
                ),
                _line(
                    "파생 근거로 복구한 제외 파일",
                    len(project.get("metrics", {}).get("resolved_skipped_files", [])),
                ),
                _line(
                    "미해결 제외 파일",
                    project.get("metrics", {}).get("unresolved_skipped_file_count", 0),
                ),
                _line("Claim", counts["claims"]),
                _line("지식 항목", counts["knowledge_items"]),
                "",
                "#### 등록된 지식 내용",
                "",
            ]
        )
        if project["knowledge"]:
            for item in project["knowledge"]:
                lines.extend(
                    [
                        f"- [{item['knowledge_type']}] {item['title']}",
                        f"  - 요약: {item['summary']}",
                        f"  - 처리 단계: {_json_inline(item['processing_steps'])}",
                        f"  - 구성요소: {_json_inline(item['components'])}",
                        f"  - 설정: {_json_inline(item['configurations'])}",
                        f"  - 의존성: {_json_inline(item['dependencies'])}",
                        f"  - 근거: {_evidence_text(item['source_references'])}",
                        f"  - 미확인: {_json_inline(item['unknowns'])}",
                    ]
                )
        else:
            lines.append("- 등록된 지식 없음")
        lines.extend(["", "#### Source-verified Qwen Claim", ""])
        if project["verified_claims"]:
            for claim in project["verified_claims"]:
                lines.extend(
                    [
                        f"- [{claim['claim_type']}] {claim['component']}: {claim['claim_text']}",
                        f"  - 근거: {_evidence_text(claim['evidence'])}",
                        f"  - 설정: {_json_inline(claim['related_configs'])}",
                        f"  - 가정: {_json_inline(claim['assumptions'])}",
                        f"  - 반대 근거: {_json_inline(claim['counter_evidence'])}",
                        f"  - 미확인: {_json_inline(claim['unknowns'])}",
                    ]
                )
        else:
            lines.append("- 검증된 Claim 없음")
        lines.append("")
    lines.extend(
        [
            "## 2. 자동 검증된 내용",
            "",
            *[
                _line(
                    project["display_name"],
                    f"{project['counts']['verified_claims']}/{project['counts']['claims']} Claim",
                )
                for project in projects
            ],
            *[
                _line(
                    f"{project['display_name']} 상태별",
                    _json_inline(project["claim_statuses"]),
                )
                for project in projects
            ],
            "",
            "## 3. Codex가 개입한 task",
            "",
        ]
    )
    for project in projects:
        intervened = [item for item in project["task_details"] if item["codex_intervened"]]
        lines.append(_line(project["display_name"], len(intervened)))
        for item in intervened:
            lines.append(
                f"  - {item['analysis_unit']}: 상태={item['status']}, "
                f"실패={item['failure_code']}, 시도={item['attempt_count']}"
            )
    lines.extend(["", "## 4. Codex가 직접 읽은 최소 소스 범위", ""])
    for project in projects:
        scopes = [
            {
                "analysis_unit": item["analysis_unit"],
                "files": item["codex_source_scope"],
            }
            for item in project["task_details"]
            if item["codex_source_scope"]
        ]
        lines.append(_line(project["display_name"], _json_inline(scopes)))
    lines.extend(
        [
            "",
            "## 5. Codex가 대신 작성하거나 수정한 Claim",
            "",
            *[
                _line(
                    project["display_name"],
                    sum(item["codex_claims_authored"] for item in project["task_details"]),
                )
                for project in projects
            ],
            "",
            "## 6. 추가 증거가 필요한 항목",
            "",
        ]
    )
    for project in projects:
        unresolved = [
            item
            for item in project["task_details"]
            if item["status"] == "ADDITIONAL_ANALYSIS_REQUIRED"
        ]
        lines.append(_line(project["display_name"], len(unresolved)))
        for item in unresolved:
            lines.append(
                f"  - {item['analysis_unit']}: 실패={item['failure_code']}, "
                f"시도={item['attempt_count']}, "
                f"추가증거={_json_inline(item['additional_evidence_requests'])}, "
                f"재시도={_json_inline(item['retry_history'])}, "
                f"실패이력={_json_inline(item['failure_history'])}, "
                f"모순={_json_inline(item['contradictions'])}"
            )
    lines.extend(
        [
            "",
            "## 7. 해결하지 못하고 넘어간 항목",
            "",
        ]
    )
    for project in projects:
        unresolved = [
            item
            for item in project["task_details"]
            if item["status"] == "ADDITIONAL_ANALYSIS_REQUIRED"
        ]
        lines.append(
            _line(
                project["display_name"],
                _json_inline(
                    {
                        "analysis_tasks": unresolved,
                        "evaluation_failures": project["evaluation_failures"],
                    }
                ),
            )
        )
    lines.extend(
        [
            "",
            "## 8. 지식베이스 등록 수",
            "",
            *[
                _line(
                    project["display_name"],
                    project["counts"]["searchable_knowledge"],
                )
                for project in projects
            ],
            "",
            "## 9. Vector DB 등록 수",
            "",
            *[
                _line(project["display_name"], project["counts"]["embeddings"])
                for project in projects
            ],
            "",
            "## 10. 기술지원 평가 결과",
            "",
        ]
    )
    for project in projects:
        missing = report["completeness"]["missing_support_categories"].get(project["category"], [])
        lines.extend(
            [
                f"### {project['display_name']}",
                "",
                _line("누락된 필수 범주", _json_inline(missing)),
                "",
                "#### 범주별 결과",
                "",
                *[
                    _line(
                        item["question_type"],
                        f"{item['passed']}/{item['result_count']} 통과; "
                        f"scope={_json_inline(item['scopes'])}",
                    )
                    for item in project["evaluation_categories"]
                ],
                "",
                "#### 실행 단계별 결과",
                "",
                "```json",
                json.dumps(
                    project["evaluation_scopes"],
                    ensure_ascii=False,
                    default=str,
                    indent=2,
                ),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## 11. 실패 원인별 통계",
            "",
        ]
    )
    for project in projects:
        lines.append(
            _line(
                project["display_name"],
                json.dumps(
                    {
                        "tasks": project["task_statuses"],
                        "evaluations": project["evaluation_failures"],
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        )
    lines.extend(
        [
            "",
            "## 12. 다음 분석 우선순위",
            "",
            "1. 보고서에 기록된 ADDITIONAL_ANALYSIS_REQUIRED task",
            "2. 누락된 필수 기술지원 평가 범주",
            "3. INSUFFICIENT_EVIDENCE 또는 RETRIEVAL_FAILURE 평가",
            "4. 운영 로그·배포 override·DB/브로커 설정이 필요한 unknown",
            "",
            "### 완전성 상세",
            "",
            "```json",
            json.dumps(
                report["completeness"],
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
            "```",
            "",
            "## 완료 조건 불변식",
            "",
            *[_line(key, value) for key, value in report["invariants"].items()],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = collect()
    rendered = (
        json.dumps(report, ensure_ascii=False, default=str, indent=2)
        if args.json
        else render_markdown(report)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"output": str(args.output)}, ensure_ascii=False))
    else:
        print(rendered)


if __name__ == "__main__":
    main()
