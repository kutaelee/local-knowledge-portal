import json
from pathlib import Path

from lkp_indexer.hook_collector import (
    _changed_files,
    _exit_code,
    activity_signal,
)


def test_apply_patch_response_extracts_exit_code_and_changed_files():
    payload = {
        "tool_input": {
            "input": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch\n"
        },
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
        "transcript_path": (
            r"\\?\C:\Users\kutae\.codex\sessions\2026\07\24\rollout.jsonl"
        ),
        "tool_use_id": "call-123",
    }
    assert _exit_code(payload, sessions) == 0
