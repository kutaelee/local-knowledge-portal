import json
import uuid
from datetime import datetime, timezone

from lkp.main import _journal_json, _redact_rag_contexts, _redact_search_response
from lkp.models import ProjectJournalEntry
from lkp.redaction import redact_text, redact_value
from lkp.schemas import Provenance, SearchResponse, SearchResult
from lkp_indexer.project_journal import clean_journal_summary


def test_pairing_token_is_redacted_in_collected_and_display_text():
    secret = "example-pairing-value-123456"
    value = f"pairing token: `{secret}`"
    assert secret not in redact_text(value)
    assert "[REDACTED]" in redact_text(value)
    assert secret not in clean_journal_summary(value)


def test_recursive_redaction_masks_secret_fields_and_bounded_metadata():
    secret = "example-pairing-value-123456"
    value = redact_value({"authorization": f"Bearer {secret}", "nested": {"token": secret}})
    assert value["authorization"] == "[REDACTED]"
    assert value["nested"]["token"] == "[REDACTED]"


def test_journal_api_hides_legacy_presentation_and_secret_text():
    secret = "example-pairing-value-123456"
    row = ProjectJournalEntry(
        id=uuid.uuid4(),
        source_stop_activity_id=uuid.uuid4(),
        project_key="sample",
        occurred_at=datetime.now(timezone.utc),
        title="운영 변경",
        intent=f"pairing token: {secret}",
        change_summary=f"token={secret}",
        failures_json=[],
        resolution=f"authorization: Bearer {secret}",
        verification_json=[],
        changed_files=[],
        knowledge_references_json=[],
        significance_reasons=[],
        verification_status="VERIFIED",
        metadata_json={
            "journal_presentation_v1": {"intent": f"token={secret}"},
            "raw_activity_preserved": True,
        },
    )
    serialized = json.dumps(_journal_json(row), ensure_ascii=False, default=str)
    assert secret not in serialized
    assert "journal_presentation_v1" not in serialized
    assert "[REDACTED]" in serialized


def test_search_results_are_redacted_before_rag_or_ui_serialization():
    secret = "example-pairing-value-123456"
    response = SearchResponse(
        query="sample",
        mode="keyword",
        confidence="low",
        total=1,
        results=[
            SearchResult(
                title="sample.md",
                project="sample",
                tags=[],
                heading_or_symbol=None,
                snippet=f"pairing token: {secret}",
                lexical_rank=1,
                vector_similarity=None,
                fused_rank=1,
                match_reason=["full-text match"],
                provenance=Provenance(
                    document_id=uuid.uuid4(),
                    document_version_id=uuid.uuid4(),
                    chunk_id=uuid.uuid4(),
                    source_root="test",
                    canonical_path="/test/sample.md",
                    relative_path="sample.md",
                    start_line=1,
                    end_line=1,
                    content_hash="hash",
                    indexed_timestamp="2026-07-25T00:00:00Z",
                ),
            )
        ],
    )
    assert secret not in _redact_search_response(response).results[0].snippet


def test_expanded_rag_context_is_redacted_after_raw_chunk_lookup():
    secret = "example-pairing-value-123456"
    contexts = [
        {
            "content": f"authorization: Bearer {secret}",
            "provenance": {"chunk_id": "current"},
        }
    ]

    redacted = _redact_rag_contexts(contexts)

    assert secret not in redacted[0]["content"]
    assert "[REDACTED]" in redacted[0]["content"]
