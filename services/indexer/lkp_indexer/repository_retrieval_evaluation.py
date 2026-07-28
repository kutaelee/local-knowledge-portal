"""Post-persistence retrieval and Qwen support-answer evaluation.

The prepare phase runs with the embedding model inside gpuq. It creates a
source-free package from the latest valid snapshot and records retrieval
quality. The answer phase runs later with Qwen inside gpuq, consumes only that
package, validates every citation against the retrieved knowledge, and records
answer quality.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from lkp.settings import get_settings
from sqlalchemy import text

from .cli import get_embedder
from .repository_analysis.provider import LocalModelProvider

_REQUIRED_ARRAYS = (
    "confirmed_facts",
    "hypotheses",
    "counter_evidence",
    "source_references",
    "configurations",
    "additional_data",
    "next_steps",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _reference_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("file"),
        value.get("source_hash"),
        int(value.get("start_line", 0)),
        int(value.get("end_line", 0)),
        value.get("symbol"),
    )


def _knowledge_rows(
    session: Any,
    *,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    query: str,
    query_vector: list[float],
    embedding_revision: str,
    limit: int,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT k.id, k.knowledge_type, k.title, k.summary, k.detail,
                       k.processing_steps, k.components, k.configurations,
                       k.dependencies, k.source_references, k.validation_status,
                       k.confidence, k.unknowns,
                       1 - (e.embedding <=> CAST(:query_vector AS vector))
                         AS vector_similarity,
                       (
                         SELECT count(*)
                         FROM regexp_split_to_table(:query, E'\\s+') AS term
                         WHERE length(term) >= 2
                           AND concat_ws(
                             ' ', k.title, k.summary, k.detail,
                             k.processing_steps::text,
                             array_to_string(k.configurations, ' '),
                             array_to_string(k.dependencies, ' ')
                           ) ILIKE '%' || term || '%'
                       ) AS keyword_matches
                FROM repository_knowledge_item k
                JOIN repository_snapshot s ON s.id = k.snapshot_id
                JOIN repository_knowledge_embedding e
                  ON e.knowledge_item_id = k.id
                 AND e.embedding_revision = :embedding_revision
                WHERE k.searchable = true
                  AND k.validation_status != 'REJECTED'
                  AND s.stale = false
                  AND s.project_id = :project_id
                  AND s.id = :snapshot_id
                ORDER BY
                  CASE WHEN k.title ILIKE '%' || :query || '%' THEN 0 ELSE 1 END,
                  keyword_matches DESC,
                  e.embedding <=> CAST(:query_vector AS vector),
                  k.created_at DESC
                LIMIT :limit
                """
            ),
            {
                "project_id": project_id,
                "snapshot_id": snapshot_id,
                "query": query,
                "query_vector": _json(query_vector),
                "embedding_revision": embedding_revision,
                "limit": limit,
            },
        ).mappings()
    ]


def _expected_rank(
    expected: list[dict[str, Any]],
    retrieved: list[dict[str, Any]],
) -> int | None:
    expected_keys = {_reference_key(item) for item in expected}
    for rank, item in enumerate(retrieved, start=1):
        item_keys = {
            _reference_key(reference)
            for reference in item.get("source_references") or []
        }
        if expected_keys & item_keys:
            return rank
    return None


def _insert_result(
    session: Any,
    *,
    case_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    passed: bool,
    score: float,
    failure_category: str | None,
    details: dict[str, Any],
    duration_ms: int,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO repository_evaluation_result (
              id, case_id, snapshot_id, passed, score, failure_category,
              details, duration_ms, created_at
            ) VALUES (
              :id, :case_id, :snapshot_id, :passed, :score, :failure_category,
              CAST(:details AS jsonb), :duration_ms, :created_at
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "case_id": case_id,
            "snapshot_id": snapshot_id,
            "passed": passed,
            "score": score,
            "failure_category": failure_category,
            "details": _json(details),
            "duration_ms": duration_ms,
            "created_at": datetime.now(timezone.utc),
        },
    )


def prepare_package(output: Path, *, limit: int = 5) -> dict[str, Any]:
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise RuntimeError("retrieval preparation must run in the admitted GPU profile")
    embedder = get_embedder(settings, deterministic=False)
    package: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "embedding_revision": settings.embedding_revision,
        "provider": embedder.provider,
        "model": embedder.model,
        "model_digest": embedder.digest,
        "dimension": embedder.dimension,
        "projects": [],
    }
    with SessionLocal() as session:
        projects = [
            dict(row)
            for row in session.execute(
                text(
                    """
                    SELECT p.id AS project_id, p.display_name, s.id AS snapshot_id,
                           s.source_hash
                    FROM repository_project p
                    JOIN repository_snapshot s ON s.project_id = p.id
                    WHERE s.stale = false
                      AND p.category LIKE 'IndigoESB %'
                    ORDER BY p.display_name
                    """
                )
            ).mappings()
        ]
        for project in projects:
            cases = [
                dict(row)
                for row in session.execute(
                    text(
                        """
                        SELECT c.id, c.question, c.question_type,
                               c.expected_evidence, c.grading_criteria
                        FROM repository_evaluation_case c
                        WHERE c.project_id = :project_id
                        ORDER BY c.created_at DESC, c.question_type
                        """
                    ),
                    {"project_id": project["project_id"]},
                ).mappings()
            ]
            # Keep the newest case per required category when a project has
            # multiple immutable snapshots.
            newest: dict[str, dict[str, Any]] = {}
            for case in cases:
                newest.setdefault(case["question_type"], case)
            project_package = {
                **project,
                "cases": [],
            }
            for case in newest.values():
                started = time.perf_counter()
                vector = embedder.embed([case["question"]])[0]
                retrieved = _knowledge_rows(
                    session,
                    project_id=project["project_id"],
                    snapshot_id=project["snapshot_id"],
                    query=case["question"],
                    query_vector=vector,
                    embedding_revision=settings.embedding_revision,
                    limit=limit,
                )
                rank = _expected_rank(case["expected_evidence"], retrieved)
                passed = rank is not None
                score = 0.0 if rank is None else 1.0 / rank
                details = {
                    "scope": "post_persistence_hybrid_retrieval",
                    "answer_quality_evaluated": False,
                    "retrieved_count": len(retrieved),
                    "expected_evidence_rank": rank,
                    "recall_at_k": 1.0 if passed else 0.0,
                    "reciprocal_rank": score,
                    "embedding_revision": settings.embedding_revision,
                    "project_id": str(project["project_id"]),
                    "snapshot_id": str(project["snapshot_id"]),
                }
                _insert_result(
                    session,
                    case_id=case["id"],
                    snapshot_id=project["snapshot_id"],
                    passed=passed,
                    score=score,
                    failure_category=None if passed else "RETRIEVAL_FAILURE",
                    details=details,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                project_package["cases"].append(
                    {
                        "case_id": case["id"],
                        "question": case["question"],
                        "question_type": case["question_type"],
                        "expected_evidence": case["expected_evidence"],
                        "retrieval": details,
                        "knowledge": retrieved,
                    }
                )
            package["projects"].append(project_package)
        session.commit()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_json(package), encoding="utf-8")
    return {
        "output": str(output),
        "projects": len(package["projects"]),
        "cases": sum(len(item["cases"]) for item in package["projects"]),
        "retrieval_passed": sum(
            case["retrieval"]["expected_evidence_rank"] is not None
            for project in package["projects"]
            for case in project["cases"]
        ),
    }


def _valid_source_reference(
    session: Any,
    *,
    snapshot_id: uuid.UUID,
    reference: dict[str, Any],
) -> bool:
    return (
        session.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM repository_source_file f
                  WHERE f.snapshot_id = :snapshot_id
                    AND f.relative_path = :file
                    AND f.content_hash = :source_hash
                    AND :start_line >= 1
                    AND :end_line >= :start_line
                    AND :end_line <= f.line_count
                    AND (
                      CAST(:symbol AS text) IS NULL OR EXISTS (
                        SELECT 1 FROM repository_source_symbol s
                        WHERE s.snapshot_id = f.snapshot_id
                          AND s.relative_path = f.relative_path
                          AND s.symbol = CAST(:symbol AS text)
                      )
                    )
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "file": reference.get("file"),
                "source_hash": reference.get("source_hash"),
                "start_line": int(reference.get("start_line", 0)),
                "end_line": int(reference.get("end_line", 0)),
                "symbol": reference.get("symbol"),
            },
        ).scalar_one()
        is True
    )


def _answer_failures(
    answer: dict[str, Any],
    *,
    allowed_references: set[tuple[Any, ...]],
    reference_validator: Any,
    evidence_texts: list[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    references = answer["source_references"]
    if not answer["confirmed_facts"]:
        failures.append("confirmed_fact_missing")
    if not references:
        failures.append("source_reference_missing")
    if not answer["next_steps"]:
        failures.append("next_verification_step_missing")
    if evidence_texts:
        evidence_terms = set(
            re.findall(
                r"[\w./:-]{2,}",
                " ".join(evidence_texts).casefold(),
            )
        )
        for index, fact in enumerate(answer["confirmed_facts"]):
            if not isinstance(fact, str):
                failures.append(f"invalid_confirmed_fact_schema:{index}")
                continue
            fact_terms = set(re.findall(r"[\w./:-]{2,}", fact.casefold()))
            overlap = len(fact_terms.intersection(evidence_terms)) / max(
                1,
                len(fact_terms),
            )
            if overlap < 0.35:
                failures.append(f"unsupported_confirmed_fact:{index}")
    for reference in references:
        if not isinstance(reference, dict):
            failures.append("invalid_source_reference_schema")
            continue
        if _reference_key(reference) not in allowed_references:
            failures.append("unretrieved_or_unverified_reference")
            continue
        if not reference_validator(reference):
            failures.append("invalid_source_reference")
    return sorted(set(failures))


def answer_package(package_path: Path) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    provider = LocalModelProvider.from_environment()
    if provider is None:
        raise RuntimeError("REPO_ANALYSIS_MODEL_ENABLED must be true")
    summary: dict[str, Any] = {
        "projects": 0,
        "cases": 0,
        "passed": 0,
        "retries": 0,
        "failure_counts": Counter(),
        "model": provider.model,
        "prompt_version": provider.prompt_version,
    }
    with SessionLocal() as session:
        for project in package["projects"]:
            summary["projects"] += 1
            project_id = uuid.UUID(str(project["project_id"]))
            snapshot_id = uuid.UUID(str(project["snapshot_id"]))
            current = session.execute(
                text(
                    """
                    SELECT s.id
                    FROM repository_snapshot s
                    WHERE s.project_id = :project_id
                      AND s.id = :snapshot_id
                      AND s.stale = false
                    """
                ),
                {"project_id": project_id, "snapshot_id": snapshot_id},
            ).scalar_one_or_none()
            if current is None:
                raise RuntimeError("SNAPSHOT_FILTER_FAILURE")
            for case in project["cases"]:
                summary["cases"] += 1
                started = time.perf_counter()
                answer: dict[str, Any] | None = None
                rejected_answer: dict[str, Any] | None = None
                error_name: str | None = None
                failures: list[str] = []
                candidates: list[tuple[float, dict[str, Any], list[str]]] = []
                repair_failures: list[str] = []
                allowed = {
                    _reference_key(reference)
                    for item in case["knowledge"]
                    for reference in item.get("source_references") or []
                }
                for attempt in range(2):
                    knowledge = case["knowledge"] if attempt == 0 else case["knowledge"][:3]
                    try:
                        invocation = provider.analyze(
                            system=(
                                "당신은 로컬 기술지원 평가 답변기다. 제공된 검색 결과는 "
                                "하나의 프로젝트와 최신 Snapshot에서 검증된 지식이다. "
                                "이것만 사용해 답하라. "
                                "JSON 객체만 반환하고 confirmed_facts, hypotheses, "
                                "counter_evidence, source_references, configurations, "
                                "additional_data, next_steps 배열과 HIGH|MEDIUM|LOW confidence를 "
                                "포함하라. 근거가 부족하면 확정하지 말고 additional_data에 남겨라. "
                                "source_references는 제공된 값을 그대로 인용하라."
                            ),
                            context={
                                "project_id": str(project_id),
                                "snapshot_id": str(snapshot_id),
                                "question_type": case["question_type"],
                                "question": case["question"],
                                "knowledge": knowledge,
                                "retry_instruction": (
                                    None
                                    if attempt == 0
                                    else {
                                        "instruction": (
                                            "이전 답변의 검증 실패만 수정하고 제공된 근거 밖의 "
                                            "주장은 제거하라. 수정할 수 없으면 confirmed_facts를 "
                                            "비우고 additional_data에 필요한 확인을 기록하라."
                                        ),
                                        "verification_failures": repair_failures,
                                    }
                                ),
                            },
                        )
                        if not all(
                            isinstance(invocation.payload.get(field), list)
                            for field in _REQUIRED_ARRAYS
                        ):
                            raise ValueError("invalid answer schema")
                        if invocation.payload.get("confidence") not in {
                            "HIGH",
                            "MEDIUM",
                            "LOW",
                        }:
                            raise ValueError("invalid confidence")
                        current_failures = _answer_failures(
                            invocation.payload,
                            allowed_references=allowed,
                            reference_validator=lambda reference,
                            current_snapshot_id=snapshot_id: _valid_source_reference(
                                session,
                                snapshot_id=current_snapshot_id,
                                reference=reference,
                            ),
                            evidence_texts=[
                                str(value)
                                for item in knowledge
                                for value in (
                                    item.get("title"),
                                    item.get("summary"),
                                    item.get("detail"),
                                    item.get("processing_steps"),
                                )
                                if value
                            ],
                        )
                        candidate_score = max(
                            0.0,
                            1.0 - min(len(current_failures), 4) * 0.25,
                        )
                        candidates.append(
                            (candidate_score, invocation.payload, current_failures)
                        )
                        if not current_failures:
                            answer = invocation.payload
                            failures = []
                            break
                        repair_failures = current_failures
                        if attempt == 0:
                            summary["retries"] += 1
                    except Exception as exc:
                        error_name = type(exc).__name__
                        repair_failures = [f"model_answer_failure:{error_name}"]
                        if attempt == 0:
                            summary["retries"] += 1

                failure_category: str | None = None
                if answer is None:
                    if candidates:
                        _, rejected_answer, failures = max(
                            candidates,
                            key=lambda item: item[0],
                        )
                    else:
                        failures = [f"model_answer_failure:{error_name}"]
                    if failures:
                        if all(
                            failure.startswith("model_answer_failure:")
                            for failure in failures
                        ):
                            failure_category = "MODEL_REASONING_FAILURE"
                        else:
                            failure_category = (
                                "EVIDENCE_VALIDATION_FAILURE"
                                if any(
                                    "reference" in failure for failure in failures
                                )
                                else "INSUFFICIENT_EVIDENCE"
                            )
                passed = not failures
                score = max(0.0, 1.0 - min(len(set(failures)), 4) * 0.25)
                details = {
                    "scope": "post_persistence_support_answer",
                    "answer_quality_evaluated": True,
                    "project_id": str(project_id),
                    "snapshot_id": str(snapshot_id),
                    "model": provider.model,
                    "prompt_version": provider.prompt_version,
                    "retrieved_count": len(case["knowledge"]),
                    "failures": sorted(set(failures)),
                    "answer": answer,
                    "rejected_answer": rejected_answer,
                    "candidate_count": len(candidates),
                    "repair_attempted": len(candidates) > 1 or error_name is not None,
                    "no_answer": answer is None,
                }
                _insert_result(
                    session,
                    case_id=uuid.UUID(str(case["case_id"])),
                    snapshot_id=snapshot_id,
                    passed=passed,
                    score=score,
                    failure_category=failure_category,
                    details=details,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                if passed:
                    summary["passed"] += 1
                elif failure_category:
                    summary["failure_counts"][failure_category] += 1
            session.execute(
                text(
                    """
                    UPDATE repository_analysis_job
                    SET metrics = metrics || CAST(:metrics AS jsonb)
                    WHERE snapshot_id = :snapshot_id
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "metrics": _json(
                        {
                            "post_persistence_support_evaluation": True,
                            "post_persistence_support_model": provider.model,
                        }
                    ),
                },
            )
        session.commit()
    summary["failure_counts"] = dict(summary["failure_counts"])
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--limit", type=int, default=5)
    answer = subparsers.add_parser("answer")
    answer.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        if args.limit < 1 or args.limit > 20:
            raise SystemExit("--limit must be between 1 and 20")
        result = prepare_package(args.output, limit=args.limit)
    else:
        result = answer_package(args.package)
    print(_json(result))


if __name__ == "__main__":
    main()
