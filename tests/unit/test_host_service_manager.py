import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "host_service_manager.py"
SPEC = importlib.util.spec_from_file_location("host_service_manager", SCRIPT)
assert SPEC and SPEC.loader
manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


def _registry(tmp_path: Path, services: list[dict]) -> Path:
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps({"schema_version": 1, "services": services}),
        encoding="utf-8",
    )
    return path


def test_registry_rejects_duplicate_and_unknown_service_definitions(tmp_path):
    duplicate = {
        "id": "same-service",
        "label": "same",
        "kind": "read_only",
        "control": False,
        "config": {},
    }
    with pytest.raises(manager.ConfigurationError, match="duplicate"):
        manager.load_config(_registry(tmp_path, [duplicate, duplicate]))

    unknown = {**duplicate, "id": "unknown", "kind": "shell"}
    with pytest.raises(manager.ConfigurationError, match="unsupported"):
        manager.load_config(_registry(tmp_path, [unknown]))


def test_compose_command_is_built_only_from_registered_fields(tmp_path):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "repo-api",
                    "label": "Repo API",
                    "kind": "docker_compose",
                    "control": True,
                    "config": {
                        "runner": "wsl",
                        "distro": "Ubuntu",
                        "project": "repo",
                        "config_file": "/home/user/src/repo/compose.yaml",
                        "services": ["api"],
                    },
                }
            ],
        )
    )[0]

    assert manager._docker_argv(service, "start") == [
        "wsl.exe",
        "-d",
        "Ubuntu",
        "--",
        "docker",
        "compose",
        "-f",
        "/home/user/src/repo/compose.yaml",
        "--project-name",
        "repo",
        "up",
        "-d",
        "api",
    ]
    assert manager._docker_argv(service, "stop")[-2:] == ["stop", "api"]


def test_read_only_service_cannot_become_controllable(tmp_path):
    path = _registry(
        tmp_path,
        [
            {
                "id": "protected-runtime",
                "label": "Protected",
                "kind": "read_only",
                "control": True,
                "config": {},
            }
        ],
    )

    with pytest.raises(manager.ConfigurationError, match="read-only"):
        manager.load_config(path)


@pytest.mark.parametrize(
    "web_url",
    [
        "https://example.com/",
        "http://user:secret@127.0.0.1:8080/",
        "http://127.0.0.1:8080/?token=secret",
        "javascript:alert(1)",
    ],
)
def test_registry_rejects_non_loopback_or_credentialed_web_urls(tmp_path, web_url):
    with pytest.raises(manager.ConfigurationError, match="web_url"):
        manager.load_config(
            _registry(
                tmp_path,
                [
                    {
                        "id": "example-ui",
                        "label": "Example UI",
                        "kind": "read_only",
                        "control": False,
                        "web_url": web_url,
                        "config": {},
                    }
                ],
            )
        )


def test_service_status_exposes_configured_and_known_loopback_web_urls(tmp_path, monkeypatch):
    configured, known = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "example-ui",
                    "label": "Example UI",
                    "kind": "read_only",
                    "control": False,
                    "health_url": "http://127.0.0.1:8080/health",
                    "web_url": "http://127.0.0.1:8080/app/",
                    "config": {},
                },
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "read_only",
                    "control": False,
                    "health_url": "http://127.0.0.1:8188/system_stats",
                    "config": {},
                },
            ],
        )
    )
    monkeypatch.setattr(manager, "_http_probe", lambda _url: ("healthy", "HTTP 200"))

    assert manager.service_status(configured)["web_url"] == "http://127.0.0.1:8080/app/"
    assert manager.service_status(known)["web_url"] == "http://127.0.0.1:8188/"


def test_comfyui_stop_guard_rejects_nonempty_queue(tmp_path, monkeypatch):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "gpuq_workload",
                    "control": True,
                    "health_url": "http://127.0.0.1:8188/system_stats",
                    "config": {
                        "workload_key": "comfyui-server",
                        "stop_guard": "comfyui_queue_empty",
                        "queue_url": "http://127.0.0.1:8188/queue",
                    },
                }
            ],
        )
    )[0]

    class QueueResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return b'{"queue_running":[["job-1",{}]],"queue_pending":[]}'

    monkeypatch.setattr(
        manager.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: QueueResponse(),
    )

    with pytest.raises(manager.ActionRejected, match="작업"):
        manager._guard_stop(service)


def test_gpuq_stop_cancels_only_registered_workload(tmp_path, monkeypatch):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "gpuq_workload",
                    "control": True,
                    "config": {"workload_key": "comfyui-server"},
                }
            ],
        )
    )[0]
    running = {
        "id": "registered-job-id",
        "workload_key": "comfyui-server",
        "status": "running",
        "bucket": "active",
    }
    calls = []
    monkeypatch.setattr(
        manager,
        "service_status",
        lambda _service: {
            "id": "comfyui",
            "state": "healthy",
            "can_start": False,
            "can_stop": True,
        },
    )
    monkeypatch.setattr(manager, "_gpuq_job", lambda _service: running)
    monkeypatch.setattr(
        manager,
        "_run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1}
        ),
    )

    result = manager.control_service(service, "stop")

    assert result["status"] == "accepted"
    assert calls[0][0] == ["gpuq", "cancel", "registered-job-id"]


def test_gpuq_service_marks_unmanaged_http_process_as_attention(tmp_path, monkeypatch):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "gpuq_workload",
                    "control": True,
                    "health_url": "http://127.0.0.1:8188/system_stats",
                    "config": {
                        "workload_key": "comfyui-server",
                        "unmanaged_stop_argv": ["stop-comfyui"],
                    },
                }
            ],
        )
    )[0]
    monkeypatch.setattr(manager, "_gpuq_job", lambda _service: None)
    monkeypatch.setattr(manager, "_http_probe", lambda _url: ("healthy", "HTTP 200"))

    status = manager.service_status(service)

    assert status["state"] == "unmanaged"
    assert status["can_stop"] is True
    assert status["can_start"] is False


def test_gpuq_unmanaged_stop_uses_only_registered_fallback(tmp_path, monkeypatch):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "gpuq_workload",
                    "control": True,
                    "config": {
                        "workload_key": "comfyui-server",
                        "unmanaged_stop_argv": ["stop-comfyui", "-Port", "8188"],
                    },
                }
            ],
        )
    )[0]
    calls = []
    monkeypatch.setattr(
        manager,
        "service_status",
        lambda _service: {
            "id": "comfyui",
            "state": "unmanaged",
            "can_start": True,
            "can_stop": True,
        },
    )
    monkeypatch.setattr(manager, "_gpuq_job", lambda _service: None)
    monkeypatch.setattr(
        manager,
        "_run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or {"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1}
        ),
    )

    result = manager.control_service(service, "stop")

    assert result["status"] == "accepted"
    assert calls[0][0] == ["stop-comfyui", "-Port", "8188"]


def test_http_process_uses_registered_start_and_stop_commands(tmp_path, monkeypatch):
    service = manager.load_config(
        _registry(
            tmp_path,
            [
                {
                    "id": "comfyui",
                    "label": "ComfyUI",
                    "kind": "http_process",
                    "control": True,
                    "health_url": "http://127.0.0.1:8188/system_stats",
                    "config": {
                        "start_argv": ["start-comfyui", "-Port", "8188"],
                        "stop_argv": ["stop-comfyui", "-Port", "8188"],
                        "detached_child": True,
                    },
                }
            ],
        )
    )[0]
    states = iter(
        [
            {"id": "comfyui", "state": "offline"},
            {"id": "comfyui", "state": "healthy"},
        ]
    )
    calls = []
    monkeypatch.setattr(manager, "service_status", lambda _service: next(states))
    monkeypatch.setattr(
        manager,
        "_run",
        lambda argv, **kwargs: (
            calls.append((argv, kwargs))
            or {"exit_code": 0, "stdout": "", "stderr": ""}
        ),
    )

    result = manager.control_service(service, "start")

    assert result["status"] == "accepted"
    assert result["service"]["state"] == "healthy"
    assert calls == [
        (
            ["start-comfyui", "-Port", "8188"],
            {"timeout": 90.0, "cwd": None, "capture_output": False},
        )
    ]


def test_comfyui_start_helper_keeps_prompt_level_gpu_reservations():
    script = (
        Path(__file__).parents[2] / "scripts" / "start-comfyui.ps1"
    ).read_text(encoding="utf-8")

    assert "Remove-Item Env:COMFYUI_GPUQ_SERVER_MANAGED" in script
    assert "Start-Process" in script
    assert "--disable-auto-launch" in script
