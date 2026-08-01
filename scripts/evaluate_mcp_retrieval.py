from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from lkp.rag_quality import terms
from lkp_indexer.codex_mcp import PortalClient, retrieve_context, verify_answer


def _provenance_valid(context: dict[str, Any]) -> bool:
    provenance = context.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("evidence_id"):
        return False
    if provenance.get("source_kind") == "repository_analysis":
        references = provenance.get("source_references")
        return bool(references) and all(
            isinstance(item, dict)
            and isinstance(item.get("file"), str)
            and len(str(item.get("source_hash") or "")) == 64
            and isinstance(item.get("start_line"), int)
            and isinstance(item.get("end_line"), int)
            and item["start_line"] >= 1
            and item["end_line"] >= item["start_line"]
            for item in references
        )
    return bool(
        provenance.get("document_id")
        and provenance.get("chunk_id")
        and provenance.get("relative_path")
        and len(str(provenance.get("content_hash") or "")) == 64
        and int(provenance.get("start_line") or 0) >= 1
        and int(provenance.get("end_line") or 0) >= int(provenance.get("start_line") or 0)
    )


def _matches(context: dict[str, Any], case: dict[str, Any]) -> bool:
    provenance = context.get("provenance") or {}
    expected_path = case.get("expected_path_contains")
    if (
        expected_path
        and expected_path.casefold() not in str(provenance.get("relative_path") or "").casefold()
    ):
        return False
    expected_kind = case.get("expected_source_kind")
    if expected_kind and provenance.get("source_kind") != expected_kind:
        return False
    expected_types = set(case.get("expected_knowledge_types") or [])
    if expected_types:
        actual_types = {
            str(tag).split(":", 1)[1]
            for tag in context.get("tags") or []
            if str(tag).startswith("repository:")
        }
        if not expected_types.intersection(actual_types):
            return False
    return True


def _resolve_live_case_paths(
    client: PortalClient,
    cases: list[dict[str, Any]],
) -> None:
    """Resolve generated case paths at runtime without committing portal data IDs."""

    live_cases = client.get("/api/v1/knowledge/cases?page=1&page_size=200").get("items") or []
    for case in cases:
        project = case.get("expected_case_project")
        if not isinstance(project, str):
            continue
        query_terms = terms(str(case.get("query") or "")) - terms(project)
        ranked: list[tuple[float, str]] = []
        for item in live_cases:
            if not isinstance(item, dict) or item.get("project") != project:
                continue
            metadata = item.get("metadata") or {}
            path = metadata.get("materialized_path")
            if not isinstance(path, str) or not path:
                continue
            content = " ".join(
                str(item.get(field) or "")
                for field in ("title", "problem", "symptom", "root_cause", "solution")
            )
            overlap = len(query_terms.intersection(terms(content))) / max(1, len(query_terms))
            ranked.append((overlap, path))
        if ranked:
            score, path = max(ranked, key=lambda item: item[0])
            if score >= 0.20:
                case["expected_path_contains"] = path


def evaluate(base_url: str, fixture: Path) -> dict[str, Any]:
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    client = PortalClient(base_url)
    details: list[dict[str, Any]] = []
    try:
        _resolve_live_case_paths(client, cases)
        for case in cases:
            arguments = {
                "query": case["query"],
                "purpose": case.get("purpose", "general"),
                "top_k": 5,
                "max_chars": 6000,
            }
            if case.get("project"):
                arguments["project"] = case["project"]
            result = retrieve_context(client, arguments)
            contexts = result["contexts"]
            expected_case_resolved = "expected_case_project" not in case or bool(
                case.get("expected_path_contains")
            )
            matched = (
                next((item for item in contexts if _matches(item, case)), None)
                if expected_case_resolved
                else None
            )
            expected_answerable = bool(case["answerable"])
            retrieval_passed = (
                bool(matched) and not result["no_answer"]
                if expected_answerable
                else result["no_answer"] and not contexts
            )
            provenance_passed = all(_provenance_valid(item) for item in contexts)
            evidence_gate_passed = all(
                item.get("evidence_level") in {"source", "verified"} for item in contexts
            )
            verifier_status = None
            verifier_passed = not expected_answerable
            if matched is not None and result.get("retrieval_id"):
                content = str(matched.get("content") or "").strip()
                claim = content[: min(300, len(content))].strip()
                evidence_id = str(matched["provenance"]["evidence_id"])
                verification = verify_answer(
                    client,
                    {
                        "retrieval_id": result["retrieval_id"],
                        "candidates": [
                            {
                                "text": claim,
                                "claims": [{"text": claim, "citations": [evidence_id]}],
                            }
                        ],
                    },
                )
                verifier_status = verification["status"]
                verifier_passed = verifier_status == "verified"
            passed = (
                expected_case_resolved
                and retrieval_passed
                and provenance_passed
                and evidence_gate_passed
                and verifier_passed
            )
            details.append(
                {
                    "name": case["name"],
                    "category": case["category"],
                    "passed": passed,
                    "expected_answerable": expected_answerable,
                    "no_answer": result["no_answer"],
                    "confidence": result["confidence"],
                    "purpose": result["purpose"],
                    "effective_project": result["effective_project"],
                    "retrieval_ranges": result["metrics"]["ranges"],
                    "range_modes": [
                        item.get("mode")
                        for item in result["metrics"]["range_results"]
                        if isinstance(item, dict) and item.get("mode")
                    ],
                    "context_count": len(contexts),
                    "elapsed_ms": result["metrics"]["elapsed_ms"],
                    "matched_expected_evidence": matched is not None,
                    "expected_case_resolved": expected_case_resolved,
                    "provenance_valid": provenance_passed,
                    "evidence_gate_passed": evidence_gate_passed,
                    "verifier_status": verifier_status,
                }
            )
    finally:
        client.close()

    grouped: dict[str, list[bool]] = defaultdict(list)
    for item in details:
        grouped[item["category"]].append(bool(item["passed"]))
    latencies = [float(item["elapsed_ms"]) for item in details]
    return {
        "cases": len(details),
        "passed": sum(item["passed"] for item in details),
        "accuracy": sum(item["passed"] for item in details) / max(1, len(details)),
        "latency_ms": {
            "median": round(statistics.median(latencies), 2) if latencies else 0,
            "maximum": round(max(latencies), 2) if latencies else 0,
        },
        "by_category": {
            key: {
                "cases": len(values),
                "accuracy": sum(values) / len(values),
            }
            for key, values in sorted(grouped.items())
        },
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/retrieval/mcp-quality-cases.json"),
    )
    args = parser.parse_args()
    result = evaluate(args.base_url, args.fixture)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["passed"] == result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
