from pathlib import Path
from types import SimpleNamespace

import httpx
from lkp.models import JobStatus, SourceRoot
from lkp_indexer.embedding import RateLimitedEmbedder
from lkp_indexer.embedding_runtime import timeout_circuit_reason, timeout_circuit_state
from lkp_indexer.queue import finish
from lkp_indexer.selection import semantic_policy
from lkp_indexer.worker import bounded_embedding_input


class TimeoutOnBatches:
    provider = "test"
    model = "test"
    digest = "test-v1"
    dimension = 1

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if len(texts) > 1:
            raise httpx.ReadTimeout("simulated bounded provider timeout")
        return [[float(len(texts[0]))]]


def test_semantic_exclusion_preserves_repository_document_policy(tmp_path: Path):
    repository = tmp_path / "project"
    (repository / ".git").mkdir(parents=True)
    evidence = repository / "docs" / "evidence" / "run.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("evidence", encoding="utf-8")
    adr = repository / "docs" / "adr" / "0001.md"
    adr.parent.mkdir(parents=True)
    adr.write_text("adr", encoding="utf-8")
    root = SourceRoot(
        name="repositories",
        canonical_path=str(tmp_path),
        source_type="repositories",
        include_patterns=["**/*"],
        exclude_patterns=[],
        semantic_exclude_patterns=["**/docs/evidence/**"],
    )

    assert semantic_policy(evidence, root, repository_mode="docs_only") == (
        False,
        "semantic_exclude_pattern",
    )
    assert semantic_policy(adr, root, repository_mode="docs_only") == (True, None)


def test_bounded_embedding_input_keeps_chunk_provenance_text_unchanged():
    source = "heading\n" + ("a" * 100) + "\ntail"

    embedded, truncated = bounded_embedding_input(source, max_chars=64)

    assert truncated is True
    assert len(embedded) <= 64
    assert embedded.startswith("heading")
    assert embedded.endswith("tail")
    assert source == "heading\n" + ("a" * 100) + "\ntail"


def test_timeout_batch_is_split_but_single_input_is_not_retried():
    delegate = TimeoutOnBatches()
    embedder = RateLimitedEmbedder(delegate, batch_size=4, cooldown_seconds=0)

    assert embedder.embed(["a", "bb", "ccc", "dddd"]) == [
        [1.0],
        [2.0],
        [3.0],
        [4.0],
    ]
    assert delegate.calls == [
        ["a", "bb", "ccc", "dddd"],
        ["a", "bb"],
        ["a"],
        ["bb"],
        ["ccc", "dddd"],
        ["ccc"],
        ["dddd"],
    ]


def test_recovered_job_clears_current_error_but_preserves_audit_detail():
    job = SimpleNamespace(
        status=JobStatus.failed,
        error_type="ReadTimeout",
        error_message="provider timed out",
        error_details={"attempt": 1},
        lease_expires_at=object(),
    )

    finish(None, job)

    assert job.status == JobStatus.succeeded
    assert job.error_type is None
    assert job.error_message is None
    assert job.lease_expires_at is None
    assert job.error_details["recovered_error"]["type"] == "ReadTimeout"


def test_gpu_recovery_mode_keeps_cpu_embedding_deferred_after_timeout_window_expires():
    class EmptySession:
        def execute(self, _statement):
            class Result:
                @staticmethod
                def one():
                    return (0, None)

            return Result()

    settings = SimpleNamespace(
        embedding_timeout_circuit_bypass=False,
        embedding_timeout_circuit_threshold=3,
        embedding_timeout_circuit_window_seconds=60,
        embedding_runtime_mode="deferred_gpu_recovery",
    )

    state = timeout_circuit_state(
        EmptySession(), threshold=3, window_seconds=60,
        runtime_mode=settings.embedding_runtime_mode,
    )

    assert state["open"] is True
    assert state["reason"] == "gpu_recovery_pending"
    assert timeout_circuit_reason(EmptySession(), settings) == "embedding_gpu_recovery_pending"
