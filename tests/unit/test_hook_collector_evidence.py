import json
from pathlib import Path

from lkp_indexer.activity_knowledge import project_from_paths
from lkp_indexer.hook_collector import (
    _changed_files,
    _exit_code,
    _transcript_turn_instruction,
    _transcript_turn_result,
    activity_signal,
)


def test_apply_patch_response_extracts_exit_code_and_changed_files():
    payload = {
        "tool_input": {"input": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch\n"},
        "tool_response": (
            "Exit code: 0\nWall time: 0.1 seconds\nOutput:\n"
            "Success. Updated the following files:\nM src/app.py\n"
        ),
    }
    assert _exit_code(payload) == 0
    assert _changed_files(payload, "apply_patch") == ["src/app.py"]


def test_read_only_image_path_does_not_become_a_changed_file():
    payload = {"tool_input": {"path": "C:/Temp/screenshot.png"}}
    assert _changed_files(payload, "view_image") == []
    promote, reasons = activity_signal(
        {"event_name": "PostToolUse", "tool_name": "view_image", "payload": payload}
    )
    assert promote is False
    assert reasons == ["read_only_low_signal_tool"]


def test_command_filter_does_not_promote_status_checks():
    def command(value: str):
        return {
            "event_name": "PostToolUse",
            "tool_name": "shell_command",
            "payload": {"tool_input": {"command": value}, "exit_code": 0},
        }

    assert activity_signal(command("docker compose ps"))[0] is False
    assert activity_signal(command("docker compose -f compose.yaml up -d"))[0] is True
    assert activity_signal(command("pytest tests/unit"))[0] is True


def test_exit_code_can_be_resolved_from_read_only_transcript(tmp_path: Path):
    sessions = tmp_path / "sessions"
    transcript = sessions / "2026" / "07" / "24" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"payload": {"call_id": "other", "output": "Exit code: 9"}}),
                json.dumps(
                    {
                        "payload": {
                            "call_id": "call-123",
                            "output": "Exit code: 0\nWall time: 0.2 seconds\nOutput:\nok",
                        }
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    payload = {
        "transcript_path": (r"\\?\C:\Users\kutae\.codex\sessions\2026\07\24\rollout.jsonl"),
        "tool_use_id": "call-123",
    }
    assert _exit_code(payload, sessions) == 0


def test_korean_development_instruction_is_selected():
    for prompt in (
        "재부팅 복구 로직을 수정하고 회귀 테스트까지 검증해",
        "현재 채팅 외의 개발 사례도 지식으로 수집해",
    ):
        envelope = {
            "event_name": "UserPromptSubmit",
            "payload": {"prompt": prompt},
        }
        promote, reasons = activity_signal(envelope)
        assert promote is True
        assert reasons == ["reusable_work_instruction"]


def test_project_is_derived_from_changed_repository_path():
    assert (
        project_from_paths(
            [r"C:\Dev\Repos\local-voice-agent\services\api.py"],
            r"C:\Users\kutae\Documents\Codex\thread",
        )
        == "local-voice-agent"
    )
    assert (
        project_from_paths(
            ["/home/kutae/src/local-knowledge-portal/README.md"],
            None,
        )
        == "local-knowledge-portal"
    )


def test_turn_instruction_is_read_from_bounded_transcript(tmp_path: Path):
    sessions = tmp_path / "sessions"
    transcript = sessions / "2026" / "07" / "24" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "turn-1"},
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "다른 개발 작업도 증거 기반으로 사례화해",
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": "turn-1"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    payload = {"transcript_path": (r"\\?\C:\Users\kutae\.codex\sessions\2026\07\24\rollout.jsonl")}
    assert (
        _transcript_turn_instruction(payload, sessions, "turn-1")
        == "다른 개발 작업도 증거 기반으로 사례화해"
    )


def test_turn_result_prefers_utf8_transcript_over_mojibake_hook_payload(tmp_path: Path):
    sessions = tmp_path / "sessions"
    transcript = sessions / "2026" / "07" / "24" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "turn-1"},
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "turn-1",
                            "last_agent_message": "수정 완료했습니다. 재기동 검증도 통과했습니다.",
                        },
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )
    payload = {
        "transcript_path": r"\\?\C:\Users\kutae\.codex\sessions\2026\07\24\rollout.jsonl",
        "last_assistant_message": "?섏젙 ?꾨즺?덉뒿?덈떎.",
    }
    assert (
        _transcript_turn_result(payload, sessions, "turn-1")
        == "수정 완료했습니다. 재기동 검증도 통과했습니다."
    )
