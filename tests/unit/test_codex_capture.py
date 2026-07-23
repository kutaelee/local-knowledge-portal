import json
from pathlib import Path

import pytest
from lkp_indexer.codex_capture import parse_transcript, redact_text, write_managed_page


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
