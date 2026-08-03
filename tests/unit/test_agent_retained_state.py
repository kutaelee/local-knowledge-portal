from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lkp.agent_evidence import evidence_state_identity
from lkp_indexer.mcp_progressive import token_aware_select


def _evidence(
    *,
    content: str = "The selected timeout is 45 seconds.",
    project: str = "synthetic-safe",
    revision: str = "revision-1",
    source_hash: str = "hash-1",
) -> dict:
    return {
        "id": "decision-timeout",
        "evidence_id": "decision-timeout",
        "project": project,
        "content": content,
        "evidence_level": "verified",
        "selection_score": 0.95,
        "provenance": {
            "document_version_id": revision,
            "content_hash": source_hash,
        },
    }


def _load_harness_run():
    script = Path(__file__).parents[2] / "scripts" / "run_agent_task_ab.py"
    spec = importlib.util.spec_from_file_location("retained_state_agent_task_ab", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.run


def test_session_fingerprint_contains_no_evidence_text() -> None:
    item = _evidence()

    fingerprint = evidence_state_identity(item)

    assert fingerprint.startswith("evidence-state:")
    assert item["content"] not in fingerprint
    assert item["project"] not in fingerprint


def test_same_revision_is_suppressed_after_session_reuse() -> None:
    item = _evidence()

    selected, metrics = token_aware_select(
        [item],
        query="What timeout was selected?",
        top_k=1,
        token_budget=100,
        seen_evidence={evidence_state_identity(item)},
    )

    assert selected == []
    assert metrics["deduplicated_count"] == 1


def test_revision_or_body_change_invalidates_session_reuse() -> None:
    previous = _evidence()
    changed_revision = _evidence(revision="revision-2", source_hash="hash-2")
    changed_body = _evidence(content="The selected timeout is 60 seconds.")

    for current in (changed_revision, changed_body):
        selected, metrics = token_aware_select(
            [current],
            query="What timeout was selected?",
            top_k=1,
            token_budget=100,
            seen_evidence={evidence_state_identity(previous)},
        )

        assert selected == [current]
        assert metrics["deduplicated_count"] == 0


def test_harness_reports_compacted_retained_state_savings() -> None:
    evidence = _evidence(content="The selected timeout is 45 seconds. " * 12)
    task = {
        "split": "heldout",
        "task_type": "prior_decision",
        "project": "synthetic-safe",
        "query": "What timeout was selected?",
        "expected_claim": "The selected timeout is 45 seconds.",
        "session_id": "paired-history",
        "source": [],
        "ranges": {"documents": [evidence]},
    }
    payload = {
        "schema_version": "agent-task-ab-v2",
        "tasks": [
            {
                "id": "source-control",
                "split": "heldout",
                "task_type": "source_only",
                "project": "synthetic-safe",
                "query": "Where is build_index defined?",
                "expected_claim": "build_index is defined in src/indexer.py.",
                "source": [
                    {
                        "id": "source-control-evidence",
                        "content": "build_index is defined in src/indexer.py.",
                        "evidence_level": "source",
                    }
                ],
                "ranges": {},
            },
            {**task, "id": "history-first"},
            {**task, "id": "history-reuse"},
        ],
    }

    result = _load_harness_run()(payload)
    improved = result["variants"]["improved-progressive-mcp"]

    assert improved["retained_state_hits"] == 1
    assert improved["estimated_retained_state_tokens_saved"] > 0
    assert result["acceptance"]["no_quality_regression"] is True
