import pytest
from lkp_indexer.repository_reference_policy import (
    current_references_sql,
    latest_snapshot_sql,
)


def test_current_reference_policy_is_fail_closed() -> None:
    statement = current_references_sql("knowledge")

    assert "jsonb_array_length(knowledge.source_references) > 0" in statement
    assert "source.content_hash = cited.value->>'source_hash'" in statement
    assert "<= source.line_count" in statement
    assert "ELSE false" in statement


def test_latest_snapshot_policy_requires_nonstale_deterministic_latest() -> None:
    statement = latest_snapshot_sql("snapshot")

    assert "snapshot.stale IS FALSE" in statement
    assert "SELECT count(*)" in statement
    assert "eligible.stale IS FALSE" in statement
    assert "latest.project_id = snapshot.project_id" in statement
    assert "latest.created_at DESC, latest.id DESC" in statement


@pytest.mark.parametrize("value", ["k; DROP TABLE x", "k.item", "K", ""])
def test_reference_policy_rejects_unsafe_aliases(value: str) -> None:
    with pytest.raises(ValueError, match="invalid SQL alias"):
        current_references_sql(value)
