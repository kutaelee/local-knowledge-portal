import json
from pathlib import Path

import pytest
from lkp_indexer.codex_capture import (
    _write_enrichment_page,
    enrichment_path_for,
    parse_transcript,
    redact_text,
    write_managed_page,
)
from lkp_indexer.generation import GenerationResult, KnowledgeEnrichment
from lkp_indexer.hook_collector import activity_signal


def _write_transcript(path: Path) -> None:
    events = [
        {
            "timestamp": "2026-07-23T01:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": "11111111-2222-3333-4444-555555555555",
                "timestamp": "2026-07-23T01:00:00Z",
                "cwd": "C:\\Dev\\Repos\\sample",
                "originator": "Codex Desktop",
                "cli_version": "1.2.3",
            },
        },
        {
            "timestamp": "2026-07-23T01:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "never publish this"}],
            },
        },
        {
            "timestamp": "2026-07-23T01:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context>hidden</environment_context>",
                    },
                    {
                        "type": "input_text",
                        "text": "문서를 저장해줘 password=super-secret-value",
                    },
                ],
            },
        },
        {
            "timestamp": "2026-07-23T01:00:03Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [{"text": "private reasoning"}]},
        },
        {
            "timestamp": "2026-07-23T01:00:04Z",
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "tool secret"},
        },
        {
            "timestamp": "2026-07-23T01:00:05Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final",
                "content": [{"type": "output_text", "text": "저장했습니다."}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )


def test_parse_filters_internal_records_and_redacts(tmp_path: Path):
    source = tmp_path / "rollout.jsonl"
    _write_transcript(source)
    transcript = parse_transcript(source)
    assert transcript.workspace == "sample"
    assert [item.role for item in transcript.messages] == ["user", "assistant"]
    text = "\n".join(item.text for item in transcript.messages)
    assert "never publish this" not in text
    assert "private reasoning" not in text
    assert "tool secret" not in text
    assert "environment_context" not in text
    assert "super-secret-value" not in text
    assert "[REDACTED]" in text


def test_managed_page_is_idempotent_and_protected(tmp_path: Path):
    source = tmp_path / "rollout.jsonl"
    vault = tmp_path / "vault"
    _write_transcript(source)
    first = write_managed_page(source, vault)
    second = write_managed_page(source, vault)
    assert first.changed is True
    assert second.changed is False
    content = first.output_path.read_text(encoding="utf-8")
    assert "managed: true" in content
    assert "시스템 지시" in content
    first.output_path.write_text("# human document\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        write_managed_page(source, vault)


def test_redact_common_secret_shapes():
    value = redact_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnop")
    assert "abcdefghijklmnopqrstuvwxyz" not in value
    assert "sk-abcdefghijklmnop" not in value


def test_enrichment_is_separate_and_identifies_local_model(tmp_path: Path):
    source = tmp_path / "rollout.jsonl"
    vault = tmp_path / "vault"
    _write_transcript(source)
    transcript = parse_transcript(source)
    generated = GenerationResult(
        content=KnowledgeEnrichment(
            summary="요약",
            observed_facts=["관측"],
            inferences_needing_confirmation=["확인 필요"],
        ),
        provider="ollama",
        model="local-summary:latest",
        model_digest="sha256:model-v1",
    )
    result = _write_enrichment_page(transcript, "source-hash", generated, vault)
    assert result.output_path == enrichment_path_for(vault, transcript)
    content = result.output_path.read_text(encoding="utf-8")
    assert "generation_provider: ollama" in content
    assert "generation_model_digest: sha256:model-v1" in content
    assert "확인이 필요합니다" in content


def test_activity_signal_filters_noise_and_keeps_reusable_evidence():
    def envelope(event: str, **payload):
        return {"event_name": event, "payload": {"hook_event_name": event, **payload}}

    assert activity_signal(envelope("SessionStart")) == (False, ["lifecycle_only"])
    assert activity_signal(envelope("UserPromptSubmit", prompt="?"))[0] is False
    assert activity_signal(
        envelope("UserPromptSubmit", prompt="검색 실패 원인을 찾아 재발하지 않게 수정해줘")
    )[0] is True
    assert activity_signal(
        envelope(
            "UserPromptSubmit",
            prompt="오늘 확인한 문서 내용을 간단하게 다시 설명해 주세요",
        )
    ) == (False, ["general_prompt_without_knowledge_signal"])
    assert activity_signal(
        envelope(
            "PostToolUse",
            tool_name="shell_command",
            tool_input={"command": "Get-ChildItem"},
            exit_code=0,
        )
    )[0] is False
    assert activity_signal(
        envelope(
            "PostToolUse",
            tool_name="shell_command",
            tool_input={"command": "pytest tests/unit"},
            exit_code=0,
        )
    )[0] is True
    assert activity_signal(
        envelope("Stop", last_assistant_message="테스트를 실행하지 않고 성공이라고 보고했습니다.")
    )[0] is True
