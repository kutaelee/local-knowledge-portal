import json
from types import SimpleNamespace

from lkp_indexer.embedding_recovery_probe import evaluate_probe_results, write_snapshot


def _response(mode: str, similarity: float | None = 0.98, *, valid: bool = True):
    provenance = SimpleNamespace(
        document_id="document" if valid else None,
        document_version_id="version" if valid else None,
        chunk_id="chunk" if valid else None,
        source_root="root" if valid else "",
        relative_path="docs/guide.md" if valid else "",
        start_line=2 if valid else 0,
        end_line=5 if valid else 0,
        content_hash="hash" if valid else "",
        indexed_timestamp="2026-07-25T00:00:00+00:00" if valid else "",
    )
    return SimpleNamespace(
        mode=mode,
        total=1,
        results=[SimpleNamespace(vector_similarity=similarity, provenance=provenance)],
    )


def test_probe_requires_semantic_and_hybrid_vectors_with_provenance():
    result = evaluate_probe_results(_response("semantic"), _response("hybrid"))

    assert result["state"] == "verified"
    assert result["semantic"]["vector_result_count"] == 1
    assert result["hybrid"]["provenance_complete"] is True


def test_probe_fails_closed_when_a_vector_or_provenance_is_missing():
    no_vector = evaluate_probe_results(_response("semantic", None), _response("hybrid"))
    malformed = evaluate_probe_results(_response("semantic", valid=False), _response("hybrid"))

    assert no_vector["state"] == "failed"
    assert malformed["state"] == "failed"


def test_probe_snapshot_is_atomic_and_contains_only_supplied_fields(tmp_path):
    target = tmp_path / "runtime" / "embedding-recovery-validation.json"
    payload = {"state": "verified", "semantic": {"vector_result_count": 1}}

    write_snapshot(target, payload)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert not list(target.parent.glob("*.tmp"))
