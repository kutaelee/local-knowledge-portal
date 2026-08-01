from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lkp.rag_quality import infer_query_scope, reward_rerank, select_verified_answer
from lkp.repository_rag_quality import repository_reward_rerank
from lkp.schemas import SearchRequest, SearchResult


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    kind: str
    query: str
    expected_paths: tuple[str, ...]
    expected_lines: tuple[tuple[str, int, int], ...] = ()
    case_marker: str | None = None
    project_id: str | None = None
    snapshot_id: str | None = None
    query_truncated: bool = False


def _bounded_evaluation_query(value: str, maximum: int = 500) -> tuple[str, bool]:
    """Keep live evaluation queries inside the public API contract.

    Large captured case fields can contain transcript-like detail. Retaining a
    bounded head and tail preserves the symptom and resolution cues without
    turning one fixture into an invalid request or an artificial full-document
    exact match.
    """

    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized, False
    head_size = int(maximum * 0.7)
    head = normalized[:head_size].rsplit(" ", 1)[0] or normalized[:head_size]
    remaining = maximum - len(head) - 1
    tail = normalized[-remaining:].split(" ", 1)[-1] or normalized[-remaining:]
    return f"{head} {tail}"[:maximum], True


class PortalClient:
    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("evaluation is restricted to a loopback portal")
        self.base_url = base_url.rstrip("/")

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(self.base_url + path, timeout=30) as response:
            return json.load(response)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def _repository_cases(
    client: PortalClient,
    projects: list[dict[str, Any]],
) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for project in projects:
        if project.get("stale") is True:
            continue
        snapshot_id = project.get("snapshot_id")
        if not snapshot_id:
            continue
        path = (
            f"/api/v1/repository-analysis/projects/{project['id']}/evaluations"
            f"?snapshot_id={snapshot_id}"
        )
        for row in client.get(path)["items"]:
            grading = row.get("grading_criteria") or {}
            if grading.get("retrieval_answerable") is not True:
                continue
            evidence = [
                item
                for item in row.get("expected_evidence") or []
                if isinstance(item, dict) and isinstance(item.get("file"), str)
            ]
            expected_paths = tuple(
                dict.fromkeys(
                    [
                        *[
                            str(value)
                            for value in row.get("required_files") or []
                            if isinstance(value, str)
                        ],
                        *[item["file"] for item in evidence],
                    ]
                )
            )
            if not expected_paths:
                continue
            bounded_query, truncated = _bounded_evaluation_query(row["question"])
            expected_lines = tuple(
                (
                    item["file"],
                    int(item.get("start_line") or 0),
                    int(item.get("end_line") or 0),
                )
                for item in evidence
                if int(item.get("start_line") or 0) > 0
                and int(item.get("end_line") or 0) >= int(item.get("start_line") or 0)
            )
            cases.append(
                EvaluationCase(
                    kind=f"repository:{row.get('question_type') or 'unknown'}",
                    query=bounded_query,
                    expected_paths=expected_paths,
                    expected_lines=expected_lines,
                    project_id=str(project["id"]),
                    snapshot_id=str(snapshot_id),
                    query_truncated=truncated,
                )
            )
    return cases


def _knowledge_cases(client: PortalClient) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    for row in client.get("/api/v1/knowledge/cases?page=1&page_size=200")["items"]:
        marker = str(row["id"])[:8]
        metadata = row.get("metadata") or {}
        materialized = metadata.get("materialized_path")
        expected = (materialized,) if isinstance(materialized, str) else ()
        if not expected:
            continue
        for field in ("problem", "symptom", "root_cause", "solution"):
            query = row.get(field)
            if isinstance(query, str) and query.strip():
                bounded_query, truncated = _bounded_evaluation_query(query)
                cases.append(
                    EvaluationCase(
                        kind=f"knowledge:{field}",
                        query=bounded_query,
                        expected_paths=expected,
                        case_marker=marker,
                        query_truncated=truncated,
                    )
                )
    return cases


def _matches_path(relative_path: str, expected_paths: tuple[str, ...]) -> bool:
    normalized = relative_path.replace("\\", "/").casefold()
    return any(
        normalized.endswith(expected.replace("\\", "/").casefold()) for expected in expected_paths
    )


def _citation_matches(result: SearchResult, case: EvaluationCase) -> bool:
    if not _matches_path(result.provenance.relative_path, case.expected_paths):
        return False
    if not case.expected_lines:
        return True
    relative = result.provenance.relative_path.replace("\\", "/").casefold()
    for path, start, end in case.expected_lines:
        if not relative.endswith(path.replace("\\", "/").casefold()):
            continue
        if result.provenance.start_line <= end and result.provenance.end_line >= start:
            return True
    return False


def _repository_row_matches(row: dict[str, Any], case: EvaluationCase) -> bool:
    return any(
        _matches_path(str(reference.get("file") or ""), case.expected_paths)
        for reference in row.get("source_references") or []
        if isinstance(reference, dict)
    )


def _repository_citation_matches(row: dict[str, Any], case: EvaluationCase) -> bool:
    if not _repository_row_matches(row, case):
        return False
    if not case.expected_lines:
        return True
    for reference in row.get("source_references") or []:
        if not isinstance(reference, dict):
            continue
        path = str(reference.get("file") or "").replace("\\", "/").casefold()
        start = int(reference.get("start_line") or 0)
        end = int(reference.get("end_line") or 0)
        for expected_path, expected_start, expected_end in case.expected_lines:
            if (
                path.endswith(expected_path.replace("\\", "/").casefold())
                and start <= expected_end
                and end >= expected_start
            ):
                return True
    return False


def _repository_citation_valid(row: dict[str, Any]) -> bool:
    references = row.get("source_references")
    if not isinstance(references, list) or not references:
        return False
    return all(
        isinstance(reference, dict)
        and bool(reference.get("file"))
        and len(str(reference.get("source_hash") or "")) == 64
        and isinstance(reference.get("start_line"), int)
        and isinstance(reference.get("end_line"), int)
        and reference["start_line"] >= 1
        and reference["end_line"] >= reference["start_line"]
        for reference in references
    )


def _search_citation_valid(result: SearchResult) -> bool:
    provenance = result.provenance
    return bool(
        provenance.chunk_id
        and provenance.document_id
        and provenance.relative_path
        and provenance.content_hash
        and provenance.start_line >= 1
        and provenance.end_line >= provenance.start_line
    )


def _metrics(
    ranks: list[int],
    evidence_alignment: list[bool],
    citation_validity: list[bool] | None = None,
) -> dict[str, float | int]:
    total = len(ranks)
    validity = citation_validity if citation_validity is not None else []
    return {
        "cases": total,
        "recall_at_5": sum(0 < rank <= 5 for rank in ranks) / total if total else 0,
        "recall_at_10": sum(0 < rank <= 10 for rank in ranks) / total if total else 0,
        "mrr": sum(1 / rank for rank in ranks if rank > 0) / total if total else 0,
        "expected_evidence_alignment": (
            sum(evidence_alignment) / len(evidence_alignment) if evidence_alignment else 0
        ),
        "citation_validity": sum(validity) / len(validity) if validity else 0,
    }


def _verifier_metrics() -> dict[str, float | int | bool]:
    contexts = [
        {
            "content": "The worker failed because its database lease expired.",
            "evidence_level": "verified",
            "provenance": {"chunk_id": "verified-chunk"},
        }
    ]
    supported = {
        "text": "The database lease expired.",
        "claims": [
            {
                "text": "The database lease expired.",
                "citations": ["verified-chunk"],
            }
        ],
    }
    unsupported = {
        "text": "The satellite launch succeeded on Tuesday.",
        "claims": [
            {
                "text": "The satellite launch succeeded on Tuesday.",
                "citations": ["verified-chunk"],
            }
        ],
    }
    unknown_citation = {
        "text": "The database lease expired.",
        "claims": [
            {
                "text": "The database lease expired.",
                "citations": ["unknown-chunk"],
            }
        ],
    }
    repair_calls = 0

    def repair_once(
        candidate: dict[str, Any],
        failures: list[str],
    ) -> dict[str, Any]:
        nonlocal repair_calls
        repair_calls += 1
        return supported

    supported_result = select_verified_answer([supported], contexts)
    unsupported_result = select_verified_answer([unsupported], contexts)
    unknown_result = select_verified_answer([unknown_citation], contexts)
    repair_result = select_verified_answer(
        [unknown_citation],
        contexts,
        repair=repair_once,
    )
    failed_repair = select_verified_answer(
        [unsupported],
        contexts,
        repair=lambda candidate, failures: candidate,
    )
    return {
        "supported_claim_acceptance": int(not supported_result["no_answer"]),
        "unsupported_claim_rejection_accuracy": int(unsupported_result["no_answer"]),
        "unknown_citation_rejection_accuracy": int(unknown_result["no_answer"]),
        "single_repair_success": int(repair_result["repaired"] and not repair_result["no_answer"]),
        "repair_calls": repair_calls,
        "no_answer_after_failed_repair": int(failed_repair["no_answer"]),
    }


def evaluate(
    base_url: str,
    fixture: Path,
    candidate_k: int,
    workers: int,
) -> dict[str, Any]:
    client = PortalClient(base_url)
    projects = [
        item["name"]
        for item in client.get("/api/v1/search/facets")["projects"]
        if isinstance(item.get("name"), str)
    ]
    repository_projects = client.get("/api/v1/repository-analysis/projects")["items"]
    answerable = [
        *_repository_cases(client, repository_projects),
        *_knowledge_cases(client),
    ]
    raw_ranks: list[int] = []
    shadow_ranks: list[int] = []
    raw_citations: list[bool] = []
    shadow_citations: list[bool] = []
    raw_validity: list[bool] = []
    shadow_validity: list[bool] = []
    failure_top3_raw = 0
    failure_top3_shadow = 0
    failure_total = 0

    def evaluate_case(
        case: EvaluationCase,
    ) -> tuple[int, int, bool | None, bool | None, bool | None, bool | None]:
        if case.kind.startswith("repository:"):
            query = urllib.parse.urlencode(
                {
                    "q": case.query,
                    "project_id": case.project_id,
                    "snapshot_id": case.snapshot_id,
                    "mode": "hybrid",
                    "limit": 100,
                }
            )
            payload = client.get(f"/api/v1/repository-analysis/search?{query}")
            raw_rows = payload["items"]
            shadow_rows = repository_reward_rerank(
                raw_rows,
                case.query,
                limit=10,
            )
            raw_top = raw_rows[:10]
            raw_rank = next(
                (
                    index
                    for index, row in enumerate(raw_top, 1)
                    if _repository_row_matches(row, case)
                ),
                0,
            )
            shadow_rank = next(
                (
                    index
                    for index, row in enumerate(shadow_rows, 1)
                    if _repository_row_matches(row, case)
                ),
                0,
            )
            raw_citation = (
                _repository_citation_matches(raw_top[raw_rank - 1], case) if raw_rank else None
            )
            shadow_citation = (
                _repository_citation_matches(shadow_rows[shadow_rank - 1], case)
                if shadow_rank
                else None
            )
            return (
                raw_rank,
                shadow_rank,
                raw_citation,
                shadow_citation,
                _repository_citation_valid(raw_top[0]) if raw_top else None,
                _repository_citation_valid(shadow_rows[0]) if shadow_rows else None,
            )

        request = SearchRequest(query=case.query, mode="keyword", top_k=10)
        scope = infer_query_scope(case.query, projects)
        payload = client.post(
            "/api/v1/search/hybrid",
            {"query": case.query, "mode": "keyword", "top_k": candidate_k},
        )
        raw = [SearchResult.model_validate(item) for item in payload["results"]]
        merged = {str(item.provenance.chunk_id): item for item in raw}
        if scope.preferred_tags:
            scoped_payload = client.post(
                "/api/v1/search/hybrid",
                {
                    "query": case.query,
                    "mode": "keyword",
                    "top_k": candidate_k,
                    "project": scope.project,
                    "tags": list(scope.preferred_tags),
                    "tag_mode": "any",
                },
            )
            for item in scoped_payload["results"]:
                parsed = SearchResult.model_validate(item)
                merged[str(parsed.provenance.chunk_id)] = parsed
        effective = (
            request.model_copy(update={"project": scope.project}) if scope.project else request
        )
        shadow = reward_rerank(list(merged.values()), effective, scope)
        raw_top = raw[:10]
        raw_rank = next(
            (
                index
                for index, result in enumerate(raw_top, 1)
                if _matches_path(result.provenance.relative_path, case.expected_paths)
            ),
            0,
        )
        shadow_rank = next(
            (
                index
                for index, result in enumerate(shadow, 1)
                if _matches_path(result.provenance.relative_path, case.expected_paths)
            ),
            0,
        )
        if shadow_rank:
            shadow_citation = _citation_matches(shadow[shadow_rank - 1], case)
        else:
            shadow_citation = None
        raw_citation = _citation_matches(raw_top[raw_rank - 1], case) if raw_rank else None
        return (
            raw_rank,
            shadow_rank,
            raw_citation,
            shadow_citation,
            _search_citation_valid(raw_top[0]) if raw_top else None,
            _search_citation_valid(shadow[0]) if shadow else None,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        evaluated = list(executor.map(evaluate_case, answerable))

    for case, (
        raw_rank,
        shadow_rank,
        raw_citation,
        shadow_citation,
        raw_is_valid,
        shadow_is_valid,
    ) in zip(
        answerable,
        evaluated,
        strict=True,
    ):
        raw_ranks.append(raw_rank)
        shadow_ranks.append(shadow_rank)
        if raw_citation is not None:
            raw_citations.append(raw_citation)
        if shadow_citation is not None:
            shadow_citations.append(shadow_citation)
        if raw_is_valid is not None:
            raw_validity.append(raw_is_valid)
        if shadow_is_valid is not None:
            shadow_validity.append(shadow_is_valid)
        if case.kind.startswith("knowledge:"):
            failure_total += 1
            failure_top3_raw += int(0 < raw_rank <= 3)
            failure_top3_shadow += int(0 < shadow_rank <= 3)

    grouped: dict[str, dict[str, list[Any]]] = defaultdict(
        lambda: {
            "raw_ranks": [],
            "shadow_ranks": [],
            "raw_citations": [],
            "shadow_citations": [],
            "raw_validity": [],
            "shadow_validity": [],
        }
    )
    for case, (
        raw_rank,
        shadow_rank,
        raw_citation,
        shadow_citation,
        raw_is_valid,
        shadow_is_valid,
    ) in zip(
        answerable,
        evaluated,
        strict=True,
    ):
        group = grouped[case.kind]
        group["raw_ranks"].append(raw_rank)
        group["shadow_ranks"].append(shadow_rank)
        if raw_citation is not None:
            group["raw_citations"].append(raw_citation)
        if shadow_citation is not None:
            group["shadow_citations"].append(shadow_citation)
        if raw_is_valid is not None:
            group["raw_validity"].append(raw_is_valid)
        if shadow_is_valid is not None:
            group["shadow_validity"].append(shadow_is_valid)

    no_answer_queries = json.loads(fixture.read_text(encoding="utf-8"))["no_answer"]
    raw_no_answer = 0
    shadow_no_answer = 0
    for query in no_answer_queries:
        request = SearchRequest(query=query, mode="keyword", top_k=10)
        scope = infer_query_scope(query, projects)
        payload = client.post(
            "/api/v1/search/hybrid",
            {"query": query, "mode": "keyword", "top_k": candidate_k},
        )
        raw = [SearchResult.model_validate(item) for item in payload["results"]]
        raw_no_answer += int(not raw[:10])
        shadow_no_answer += int(not reward_rerank(raw, request, scope))

    return {
        "corpus": {
            "repository_cases": sum(case.kind.startswith("repository:") for case in answerable),
            "knowledge_cases": sum(case.kind.startswith("knowledge:") for case in answerable),
            "no_answer_cases": len(no_answer_queries),
            "truncated_queries": sum(case.query_truncated for case in answerable),
            "stale_repository_projects_excluded": sum(
                item.get("stale") is True for item in repository_projects
            ),
        },
        "before": {
            **_metrics(raw_ranks, raw_citations, raw_validity),
            "failure_top3": failure_top3_raw / failure_total if failure_total else 0,
            "no_answer_accuracy": raw_no_answer / len(no_answer_queries),
        },
        "shadow": {
            **_metrics(shadow_ranks, shadow_citations, shadow_validity),
            "failure_top3": (failure_top3_shadow / failure_total if failure_total else 0),
            "no_answer_accuracy": shadow_no_answer / len(no_answer_queries),
        },
        "by_kind": {
            kind: {
                "before": _metrics(
                    values["raw_ranks"],
                    values["raw_citations"],
                    values["raw_validity"],
                ),
                "shadow": _metrics(
                    values["shadow_ranks"],
                    values["shadow_citations"],
                    values["shadow_validity"],
                ),
            }
            for kind, values in sorted(grouped.items())
        },
        "answer_verifier": _verifier_metrics(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/retrieval/quality-cases.json"),
    )
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 10 <= args.candidate_k <= 100:
        parser.error("--candidate-k must be between 10 and 100")
    if not 1 <= args.workers <= 12:
        parser.error("--workers must be between 1 and 12")
    result = evaluate(args.base_url, args.fixture, args.candidate_k, args.workers)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
