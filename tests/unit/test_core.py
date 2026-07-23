from pathlib import Path

import pytest
from lkp_indexer.chunking import chunk_code, chunk_markdown
from lkp_indexer.ignore import IgnoreRules
from lkp_indexer.paths import UnsafePathError, canonicalize, content_hash, idempotency_key
from lkp_indexer.queue import retry_delay
from lkp_indexer.worker import embedding_cost_decision


def test_canonicalization_rejects_escape(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    with pytest.raises(UnsafePathError):
        canonicalize(outside, root)


def test_ignore_defaults(tmp_path: Path):
    rules = IgnoreRules(tmp_path)
    assert rules.matches("node_modules/pkg/index.js")
    assert rules.matches("project/tokenizer_configs/stable_diffusion/merges.txt")
    assert rules.matches("project/tokenizer/merges.txt")
    assert not rules.matches("docs/readme.md")


def test_hash_and_idempotency_stable(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_text("same", encoding="utf-8")
    assert content_hash(path) == content_hash(path)
    assert idempotency_key("r", "C:\\A", 4, 1) == idempotency_key("r", "c:\\a", 4, 1)


def test_retry_backoff_is_bounded():
    assert [retry_delay(i) for i in range(1, 5)] == [2, 4, 8, 16]
    assert retry_delay(100) == 300


def test_markdown_heading_lines():
    chunks, metadata = chunk_markdown("---\ntag: test\n---\n# A\nbody\n## B\nmore")
    assert metadata["tag"] == "test"
    assert chunks[-1].heading_path == "A / B"
    assert chunks[-1].start_line == 6


def test_code_symbols():
    chunks = chunk_code("def first():\n    pass\n\ndef second():\n    pass\n", ".py")
    assert [chunk.symbol_name for chunk in chunks] == ["first", "second"]
    assert chunks[0].start_line == 1


def test_embedding_cost_limit_prevents_unbounded_document_jobs():
    class Chunk:
        def __init__(self, content: str) -> None:
            self.content = content

    assert embedding_cost_decision(
        [Chunk("small"), Chunk("document")], max_chunks=4, max_chars=100,
        path=Path("docs/runbook.md"),
    ) == (True, None)
    assert embedding_cost_decision(
        [Chunk("x")] * 5, max_chunks=4, max_chars=100
    ) == (False, "chunk_limit")
    assert embedding_cost_decision(
        [Chunk("x" * 60), Chunk("y" * 60)], max_chunks=4, max_chars=100
    ) == (False, "character_limit")
    assert embedding_cost_decision(
        [Chunk("dependencies")], max_chunks=4, max_chars=100,
        path=Path("pnpm-lock.yaml"),
    ) == (False, "low_value_artifact")
