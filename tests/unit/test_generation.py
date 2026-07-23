import json
from pathlib import Path

import httpx
import pytest
from lkp.settings import Settings
from lkp_indexer.generation import (
    OllamaGenerationProvider,
    build_generation_provider,
)


def test_generation_is_disabled_by_default():
    assert build_generation_provider(Settings(generation_provider="disabled")) is None


def test_global_codex_homes_and_local_model_guard():
    settings = Settings(
        codex_home=Path("C:/Users/example/.codex"),
        codex_additional_homes="C:/WSL/Ubuntu/codex;D:/isolated-codex",
    )
    assert len(settings.codex_home_list) == 3
    with pytest.raises(ValueError, match="localhost"):
        Settings(generation_base_url="https://remote-model.example")


def test_ollama_generation_uses_structured_output_and_records_digest():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "local-summary:latest", "digest": "sha256:model-v1"}
                    ]
                },
            )
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["options"]["temperature"] == 0
        assert payload["format"]["type"] == "object"
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "summary": "검증된 요약",
                            "observed_facts": ["테스트가 실행됐다."],
                            "extracted_information": [],
                            "inferences_needing_confirmation": ["원인은 추가 확인이 필요하다."],
                        },
                        ensure_ascii=False,
                    ),
                }
            },
        )

    provider = OllamaGenerationProvider(
        "http://127.0.0.1:11434",
        "local-summary:latest",
        "unresolved",
        5,
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate("source")
    assert result.model_digest == "sha256:model-v1"
    assert result.content.summary == "검증된 요약"
    assert result.content.inferences_needing_confirmation


def test_ollama_generation_fails_closed_on_digest_change():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "local-summary:latest", "digest": "sha256:changed"}
                ]
            },
        )

    provider = OllamaGenerationProvider(
        "http://127.0.0.1:11434",
        "local-summary:latest",
        "sha256:expected",
        5,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeError, match="digest changed"):
        provider.generate("source")
