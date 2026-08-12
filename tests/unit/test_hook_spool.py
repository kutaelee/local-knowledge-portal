import json
from pathlib import Path

from lkp_indexer.hook_spool import MAX_INPUT_BYTES, build_envelope, spool


def payload(event: str = "PostToolUse") -> bytes:
    return json.dumps(
        {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": event,
            "cwd": r"C:\Dev\Repos\sample",
            "tool_name": "shell_command",
            "tool_input": {
                "command": "pytest",
                "authorization": "Bearer top-secret-value",
            },
            "tool_output": {"exit_code": 0},
        }
    ).encode()


def test_spool_is_atomic_redacted_and_idempotent(tmp_path: Path):
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    first = spool(payload(), primary, fallback)
    second = spool(payload(), primary, fallback)
    assert first == second
    assert len(list((primary / "pending").glob("*.json"))) == 1
    content = first.read_text(encoding="utf-8")
    assert "top-secret-value" not in content
    assert "[REDACTED]" in content


def test_spool_falls_back_when_primary_is_not_directory(tmp_path: Path):
    primary = tmp_path / "blocked"
    primary.write_text("file", encoding="utf-8")
    result = spool(payload("SessionStart"), primary, tmp_path / "fallback")
    envelope = json.loads(result.read_text(encoding="utf-8"))
    assert envelope["used_fallback"] is True
    assert result.parent.name == "pending"


def test_malformed_oversized_and_unsupported_are_quarantined(tmp_path: Path):
    malformed = spool(b"{broken", tmp_path / "p", tmp_path / "f")
    oversized = spool(b"x" * (MAX_INPUT_BYTES + 1), tmp_path / "p", tmp_path / "f")
    unsupported = spool(payload("UnknownFutureHook"), tmp_path / "p", tmp_path / "f")
    assert malformed.parent.name == "quarantine"
    assert oversized.parent.name == "quarantine"
    assert unsupported.parent.name == "quarantine"
    assert json.loads(malformed.read_text(encoding="utf-8"))["payload"] == {}
    assert json.loads(oversized.read_text(encoding="utf-8"))["status"] == "oversized"


def test_all_required_events_are_accepted():
    events = {
        "SessionStart",
        "UserPromptSubmit",
        "PostToolUse",
        "Stop",
        "SubagentStart",
        "SubagentStop",
    }
    assert {build_envelope(payload(event))[1] for event in events} == {"pending"}
