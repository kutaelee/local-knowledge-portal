from types import SimpleNamespace

from lkp_indexer import nightly_embedding_refresh as embedding_refresh
from lkp_indexer import nightly_generation_maintenance as generation
from lkp_indexer import nightly_semantic_maintenance as semantic


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def commit(self):
        return None


class _Closable:
    provider = "ollama"
    model = "test-model"

    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_semantic_maintenance_reuses_and_closes_one_embedder(monkeypatch):
    embedder = _Closable()
    settings = SimpleNamespace(embedding_timeout_circuit_bypass=True)
    observed = []
    monkeypatch.setattr(semantic, "get_settings", lambda: settings)
    monkeypatch.setattr(semantic, "assert_mount_guards", lambda _settings: None)
    monkeypatch.setattr(semantic, "get_embedder", lambda *_args, **_kwargs: embedder)
    monkeypatch.setattr(semantic, "SessionLocal", _Session)
    monkeypatch.setattr(
        semantic,
        "wait_for_ingest_quiescence",
        lambda: {"state": "quiescent", "active_jobs": 0, "waited_seconds": 0},
    )
    monkeypatch.setattr(
        semantic,
        "run_document_reindex",
        lambda _settings, value, _limit: observed.append(value) or {"embedded": 2},
    )
    monkeypatch.setattr(
        semantic,
        "run_repository_reindex",
        lambda _settings, value, _limit: observed.append(value) or {"embedded": 3},
    )
    monkeypatch.setattr(
        semantic,
        "run_dedup",
        lambda _session, _settings, value: observed.append(value) or {"passed": 1},
    )
    monkeypatch.setattr(
        semantic,
        "run_recovery_probe",
        lambda *, embedder: observed.append(embedder) or {"state": "verified"},
    )

    result, failures = semantic.run()

    assert failures == 0
    assert result["state"] == "succeeded"
    assert observed == [embedder, embedder, embedder, embedder]
    assert embedder.closed == 1


def test_generation_maintenance_reuses_and_closes_one_provider(monkeypatch):
    provider = _Closable()
    settings = SimpleNamespace(
        knowledge_curation_enabled=True,
        project_article_enabled=True,
    )
    observed = []
    monkeypatch.setattr(generation, "get_settings", lambda: settings)
    monkeypatch.setattr(generation, "assert_mount_guards", lambda _settings: None)
    monkeypatch.setattr(generation, "build_generation_provider", lambda _settings: provider)
    monkeypatch.setattr(generation, "SessionLocal", _Session)
    monkeypatch.setattr(
        generation,
        "run_curation",
        lambda _session, _settings, **kwargs: (
            observed.append(kwargs["provider"]) or {"state": "completed_batch"}
        ),
    )
    monkeypatch.setattr(
        generation,
        "refresh_all_project_articles",
        lambda _session, **kwargs: observed.append(kwargs["provider"]) or [{"status": "unchanged"}],
    )

    result, failures = generation.run()

    assert failures == 0
    assert result["state"] == "succeeded"
    assert observed == [provider, provider]
    assert provider.closed == 1


def test_post_generation_embedding_refresh_reindexes_after_quiescence(monkeypatch):
    monkeypatch.setattr(
        embedding_refresh,
        "wait_for_ingest_quiescence",
        lambda: {"state": "quiescent", "active_jobs": 0, "waited_seconds": 0},
    )
    monkeypatch.setattr(
        embedding_refresh,
        "run",
        lambda: {"embedded": 4, "still_deferred": 0},
    )

    result, exit_code = embedding_refresh.refresh()

    assert exit_code == 0
    assert result["state"] == "succeeded"
    assert result["document_reindex"]["embedded"] == 4


def test_post_generation_embedding_refresh_fails_closed_when_ingest_is_busy(
    monkeypatch,
):
    reindex_calls = []
    monkeypatch.setattr(
        embedding_refresh,
        "wait_for_ingest_quiescence",
        lambda: {"state": "timeout", "active_jobs": 1, "waited_seconds": 120},
    )
    monkeypatch.setattr(
        embedding_refresh,
        "run",
        lambda: reindex_calls.append(True),
    )

    result, exit_code = embedding_refresh.refresh()

    assert exit_code == 1
    assert result["state"] == "ingest_timeout"
    assert reindex_calls == []
