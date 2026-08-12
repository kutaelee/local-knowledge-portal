from pathlib import Path
from types import SimpleNamespace

import pytest
from lkp.models import SourceRoot
from lkp_indexer.chunking import chunk_code, chunk_markdown
from lkp_indexer.embedding import OllamaEmbedder
from lkp_indexer.ignore import IgnoreRules, IncludeRules
from lkp_indexer.paths import UnsafePathError, canonicalize, content_hash, idempotency_key
from lkp_indexer.projects import project_identity
from lkp_indexer.queue import retry_delay
from lkp_indexer.scanner import scan_bases, scan_root
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


def test_include_patterns_are_allowlist_not_metadata_only():
    rules = IncludeRules(["*.md", "**/*.md", "**/*.rst"])

    assert rules.matches("README.md")
    assert rules.matches("docs/runbook.md")
    assert rules.matches("docs/design.rst")
    assert not rules.matches("scripts/bootstrap.ps1")
    assert not rules.matches("services/api/main.py")


def test_hash_and_idempotency_stable(tmp_path: Path):
    path = tmp_path / "a.txt"
    path.write_text("same", encoding="utf-8")
    assert content_hash(path) == content_hash(path)
    assert idempotency_key("r", "C:\\A", 4, 1) == idempotency_key("r", "c:\\a", 4, 1)


def test_retry_backoff_is_bounded():
    assert [retry_delay(i) for i in range(1, 5)] == [2, 4, 8, 16]
    assert retry_delay(100) == 300


def test_ollama_embedding_request_is_nonresident_and_close_unloads(monkeypatch):
    calls = []
    resident = iter([False, True, False])

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "embeddings": [[0.25, 0.75]],
                "prompt_eval_count": 20,
                "total_duration": 110_000_000,
                "load_duration": 10_000_000,
            }

    def post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    class PsResponse:
        def __init__(self, loaded):
            self.loaded = loaded

        def raise_for_status(self):
            return None

        def json(self):
            return {"models": ([{"name": "qwen3-embedding:0.6b"}] if self.loaded else [])}

    def get(_url, *, timeout):
        assert timeout == 10
        return PsResponse(next(resident))

    monkeypatch.setattr("lkp_indexer.embedding.httpx.post", post)
    monkeypatch.setattr("lkp_indexer.embedding.httpx.get", get)
    embedder = OllamaEmbedder(
        "http://127.0.0.1:11434",
        "qwen3-embedding:0.6b",
        "digest",
        2,
        keep_alive="0",
    )

    assert embedder.embed(["query"]) == [[0.25, 0.75]]
    embedder.close()

    assert calls[0][1]["keep_alive"] == "0"
    assert calls[1][0].endswith("/api/generate")
    assert calls[1][1] == {"model": "qwen3-embedding:0.6b", "keep_alive": 0}
    assert embedder.performance_metrics() == {
        "requests": 1,
        "inputs": 1,
        "prompt_tokens": 20,
        "total_duration_ns": 110_000_000,
        "load_duration_ns": 10_000_000,
        "inputs_per_second": 10.0,
        "prompt_tokens_per_second": 200.0,
        "preexisting_resident": False,
        "unload_verified": True,
    }


def test_markdown_heading_lines():
    chunks, metadata = chunk_markdown("---\ntag: test\n---\n# A\nbody\n## B\nmore")
    assert metadata["tag"] == "test"
    assert chunks[-1].heading_path == "A / B"
    assert chunks[-1].start_line == 6


def test_markdown_retrieval_metadata_carries_project_and_value_tags():
    chunks, metadata = chunk_markdown(
        "---\n"
        "project: local-knowledge-portal\n"
        "tags: [situation:performance, platform:wsl2]\n"
        "knowledge_value_tier: promote\n"
        "knowledge_value_labels: [knowledge-value:promote]\n"
        "private_note: never-copy-to-chunks\n"
        "---\n"
        "# CPU guard\n"
        "bounded worker load"
    )
    assert metadata["private_note"] == "never-copy-to-chunks"
    assert chunks[-1].metadata == {
        "project": "local-knowledge-portal",
        "tags": ["situation:performance", "platform:wsl2"],
        "knowledge_value_tier": "promote",
        "knowledge_value_labels": ["knowledge-value:promote"],
    }


def test_single_oversized_line_is_bounded_without_losing_line_provenance():
    chunks, _ = chunk_markdown("# Generated\n" + ("x" * 15_001), max_chars=6000)
    oversized = [item for item in chunks if item.metadata.get("oversized_line_segment")]
    assert [len(item.content) for item in oversized] == [6000, 6000, 3001]
    assert all(item.start_line == 2 and item.end_line == 2 for item in oversized)
    assert [item.metadata["start_char"] for item in oversized] == [0, 6000, 12000]


def test_repository_collection_discovers_only_direct_git_repositories(tmp_path: Path):
    repository = tmp_path / "project-a"
    repository.mkdir()
    (repository / ".git").mkdir()
    worktree = tmp_path / "project-b"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    unrelated = tmp_path / "downloads"
    unrelated.mkdir()
    assert scan_bases(tmp_path, "repository_collection") == [repository, worktree]
    assert scan_bases(tmp_path, "repositories") == [tmp_path]


def test_unavailable_repository_collection_is_a_bounded_scan_error(tmp_path: Path):
    root = SourceRoot(
        name="offline repositories",
        canonical_path=str(tmp_path / "offline"),
        source_type="repository_collection",
        data_scope="validation",
        read_only=True,
        enabled=True,
        include_patterns=["**/*"],
        exclude_patterns=[],
    )
    stats = scan_root(SimpleNamespace(), root, max_file_bytes=1024)
    assert stats.errors == 1
    assert stats.visited == 0
    assert stats.queued == 0


def test_code_symbols():
    chunks = chunk_code("def first():\n    pass\n\ndef second():\n    pass\n", ".py")
    assert [chunk.symbol_name for chunk in chunks] == ["first", "second"]
    assert chunks[0].start_line == 1


def test_embedding_cost_limit_prevents_unbounded_document_jobs():
    class Chunk:
        def __init__(self, content: str) -> None:
            self.content = content

    assert embedding_cost_decision(
        [Chunk("small"), Chunk("document")],
        max_chunks=4,
        max_chars=100,
        path=Path("docs/runbook.md"),
    ) == (True, None)
    assert embedding_cost_decision([Chunk("x")] * 5, max_chunks=4, max_chars=100) == (
        False,
        "chunk_limit",
    )
    assert embedding_cost_decision(
        [Chunk("x" * 60), Chunk("y" * 60)], max_chunks=4, max_chars=100
    ) == (False, "character_limit")
    assert embedding_cost_decision(
        [Chunk("dependencies")],
        max_chunks=4,
        max_chars=100,
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
    assert semantic_policy(tmp_path / "README.md", root, repository_mode="docs_only") == (
        True,
        None,
    )
    assert semantic_policy(tmp_path / "model.py", root, repository_mode="docs_only") == (
        False,
        "repository_docs_only",
    )
    assert semantic_policy(tmp_path / "model.py", root, repository_mode="code_and_docs") == (
        True,
        None,
    )
    assert semantic_policy(nested_dependency / "README.md", root, repository_mode="docs_only") == (
        False,
        "nested_repository_dependency",
    )
    root.source_type = "repository_collection"
    assert semantic_policy(tmp_path / "model.py", root, repository_mode="docs_only") == (
        False,
        "repository_docs_only",
    )


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
