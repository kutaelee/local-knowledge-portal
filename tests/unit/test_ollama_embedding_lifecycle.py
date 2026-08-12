from __future__ import annotations

import httpx
import pytest
from lkp_indexer.embedding import ModelUnloadError, OllamaEmbedder


def _response(status: int, payload: dict, method: str, url: str) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request(method, url),
    )


def test_ollama_embedder_unloads_only_its_owned_model(monkeypatch):
    base = "http://127.0.0.1:11434"
    resident = [False, True, False]
    posts: list[dict] = []

    def fake_get(url, **_kwargs):
        loaded = resident.pop(0)
        models = [{"name": "qwen3-embedding:0.6b"}] if loaded else []
        return _response(200, {"models": models}, "GET", url)

    def fake_post(url, json, **_kwargs):
        posts.append(json)
        return _response(200, {"embeddings": [[0.1, 0.2]]}, "POST", url)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    embedder = OllamaEmbedder(base, "qwen3-embedding:0.6b", "digest", 2)

    assert embedder.embed(["query"]) == [[0.1, 0.2]]
    embedder.close()

    assert posts[-1] == {"model": "qwen3-embedding:0.6b", "keep_alive": 0}
    assert embedder.performance_metrics()["unload_verified"] is True


def test_ollama_embedder_preserves_preexisting_resident_model(monkeypatch):
    base = "http://127.0.0.1:11434"
    posts: list[dict] = []
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **_kwargs: _response(
            200,
            {"models": [{"name": "qwen3-embedding:0.6b"}]},
            "GET",
            url,
        ),
    )

    def fake_post(url, json, **_kwargs):
        posts.append(json)
        return _response(200, {"embeddings": [[0.1, 0.2]]}, "POST", url)

    monkeypatch.setattr(httpx, "post", fake_post)
    embedder = OllamaEmbedder(base, "qwen3-embedding:0.6b", "digest", 2)

    embedder.embed(["query"])
    embedder.close()

    assert len(posts) == 1
    assert embedder.performance_metrics()["preexisting_resident"] is True


def test_ollama_embedder_reports_unload_failure(monkeypatch):
    base = "http://127.0.0.1:11434"
    resident = [False, True]

    def fake_get(url, **_kwargs):
        loaded = resident.pop(0)
        models = [{"name": "qwen3-embedding:0.6b"}] if loaded else []
        return _response(200, {"models": models}, "GET", url)

    calls = 0

    def fake_post(url, json, **_kwargs):
        nonlocal calls
        calls += 1
        status = 200 if calls == 1 else 500
        payload = {"embeddings": [[0.1, 0.2]]} if status == 200 else {"error": "busy"}
        return _response(status, payload, "POST", url)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    embedder = OllamaEmbedder(base, "qwen3-embedding:0.6b", "digest", 2)
    embedder.embed(["query"])

    with pytest.raises(ModelUnloadError):
        embedder.close()

    assert embedder.performance_metrics()["unload_verified"] is False


def test_ollama_embedder_unloads_after_failed_embedding_request(monkeypatch):
    base = "http://127.0.0.1:11434"
    resident = [False, True, False]
    posts: list[str] = []

    def fake_get(url, **_kwargs):
        loaded = resident.pop(0)
        models = [{"name": "qwen3-embedding:0.6b"}] if loaded else []
        return _response(200, {"models": models}, "GET", url)

    def fake_post(url, json, **_kwargs):
        posts.append(url)
        if url.endswith("/api/embed"):
            raise httpx.ReadTimeout("request timed out", request=httpx.Request("POST", url))
        return _response(200, {"done": True, "done_reason": "unload"}, "POST", url)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    embedder = OllamaEmbedder(base, "qwen3-embedding:0.6b", "digest", 2)

    with pytest.raises(httpx.ReadTimeout):
        embedder.embed(["query"])
    embedder.close()

    assert posts == [f"{base}/api/embed", f"{base}/api/generate"]
    assert embedder.performance_metrics()["unload_verified"] is True
