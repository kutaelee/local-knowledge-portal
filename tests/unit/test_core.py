from pathlib import Path
from types import SimpleNamespace

import pytest
from lkp.models import SourceRoot
from lkp_indexer.chunking import chunk_code, chunk_markdown
from lkp_indexer.ignore import IgnoreRules
from lkp_indexer.paths import UnsafePathError, canonicalize, content_hash, idempotency_key
from lkp_indexer.projects import project_identity
from lkp_indexer.queue import retry_delay
from lkp_indexer.selection import semantic_policy
from lkp_indexer.worker import embedding_cost_decision
from lkp_indexer.worker_service import workload_policy


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
    assert rules.matches("project/.next-prod-v24/server/route.js")
    assert rules.matches("old/playwright-profile-notice/Default/leveldb/000003.log")
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


def test_project_identity_uses_top_level_git_repository(tmp_path: Path):
    source_root = tmp_path / "src"
    repository = source_root / "ai" / "FlashVSR"
    vendored_repository = repository / "vendor" / "eigen"
    source = vendored_repository / "src" / "model.py"
    (repository / ".git").mkdir(parents=True)
    (vendored_repository / ".git").mkdir(parents=True)
    source.parent.mkdir(parents=True)
    source.write_text("pass", encoding="utf-8")
    identity = project_identity(source, source_root)
    assert identity.key == "FlashVSR"
    assert identity.relative_path == "vendor/eigen/src/model.py"


def test_repository_semantic_policy_separates_code_from_docs(tmp_path: Path):
    repository = tmp_path / "project"
    nested_dependency = repository / "vendor"
    (repository / ".git").mkdir(parents=True)
    (nested_dependency / ".git").mkdir(parents=True)
    root = SourceRoot(
        name="repositories",
        canonical_path=str(tmp_path),
        source_type="repositories",
        data_scope="validation",
        read_only=True,
        enabled=True,
        include_patterns=["**/*"],
        exclude_patterns=[],
    )
    assert semantic_policy(
        tmp_path / "README.md", root, repository_mode="docs_only"
    ) == (True, None)
    assert semantic_policy(
        tmp_path / "model.py", root, repository_mode="docs_only"
    ) == (False, "repository_docs_only")
    assert semantic_policy(
        tmp_path / "model.py", root, repository_mode="code_and_docs"
    ) == (True, None)
    assert semantic_policy(
        nested_dependency / "README.md", root, repository_mode="docs_only"
    ) == (False, "nested_repository_dependency")


def test_worker_uses_separate_semantic_and_lexical_rate_limits():
    settings = SimpleNamespace(
        worker_job_cooldown_seconds=1.0,
        worker_burst_jobs=20,
        worker_burst_cooldown_seconds=15.0,
        worker_lexical_job_cooldown_seconds=0.05,
        worker_lexical_burst_jobs=200,
        worker_lexical_burst_cooldown_seconds=2.0,
    )
    semantic = workload_policy("semantic", settings)
    lexical = workload_policy("lexical", settings)
    assert semantic.cooldown_seconds == 1.0
    assert semantic.burst_jobs == 20
    assert lexical.cooldown_seconds == 0.05
    assert lexical.burst_jobs == 200
