import json
import uuid

import pytest
from lkp import main
from lkp.settings import Settings
from pydantic import ValidationError
from starlette.requests import Request


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def test_gpu_scheduler_setting_accepts_only_local_host_boundaries():
    assert (
        Settings(gpu_scheduler_base_url="http://host.docker.internal:8790").gpu_scheduler_base_url
        == "http://host.docker.internal:8790"
    )
    assert (
        Settings(gpu_scheduler_base_url="http://127.0.0.1:8790/").gpu_scheduler_base_url
        == "http://127.0.0.1:8790"
    )
    with pytest.raises(ValidationError):
        Settings(gpu_scheduler_base_url="https://scheduler.example.com")
    with pytest.raises(ValidationError):
        Settings(gpu_scheduler_base_url="http://host.docker.internal:8790/admin")


def test_service_manager_setting_accepts_only_local_host_boundaries():
    assert (
        Settings(
            service_manager_base_url="http://host.docker.internal:8791"
        ).service_manager_base_url
        == "http://host.docker.internal:8791"
    )
    with pytest.raises(ValidationError):
        Settings(service_manager_base_url="https://manager.example.com")
    with pytest.raises(ValidationError):
        Settings(service_manager_base_url="http://127.0.0.1:8791/admin")


def test_service_manager_proxy_keeps_control_token_server_side(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse({"status": "ready", "services": []})

    monkeypatch.setattr(main.settings, "service_manager_token", "server-only")
    monkeypatch.setattr(main.httpx, "request", fake_request)

    assert main.service_manager_status() == {"status": "ready", "services": []}
    assert calls == [
        (
            "GET",
            f"{main.settings.service_manager_base_url}/api/services",
            {
                "json": None,
                "headers": {"Authorization": "Bearer server-only"},
                "timeout": main.settings.service_manager_timeout_seconds,
                "follow_redirects": False,
            },
        )
    ]


def test_service_manager_actions_use_safe_stop_timeout(monkeypatch):
    calls: list[dict] = []

    def fake_request(_method: str, _url: str, **kwargs):
        calls.append(kwargs)
        return FakeResponse({"status": "accepted"})

    monkeypatch.setattr(main.settings, "service_manager_token", "server-only")
    monkeypatch.setattr(main.httpx, "request", fake_request)

    assert main._service_manager_request(
        "POST",
        "/api/services/ai-toolkit/stop",
        {"confirmed": True},
    ) == {"status": "accepted"}
    assert calls[0]["timeout"] == main.settings.service_manager_action_timeout_seconds


def test_service_confirmation_is_bound_and_one_time():
    issued = main._issue_service_confirmation("comfyui", "stop")
    token = str(issued["confirmation_token"])

    main._consume_service_confirmation(token, "comfyui", "stop")

    with pytest.raises(main.HTTPException) as exc_info:
        main._consume_service_confirmation(token, "comfyui", "stop")
    assert exc_info.value.status_code == 409


def test_service_control_never_forwards_browser_confirmation_token(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, payload: dict):
        calls.append((method, path, payload))
        return {"status": "accepted"}

    monkeypatch.setattr(main, "_service_manager_request", fake_request)
    issued = main._issue_service_confirmation("comfyui", "stop")
    request = Request(
        {
            "type": "http",
            "headers": [(b"origin", b"http://127.0.0.1:3010")],
        }
    )
    payload = main.ServiceControlRequest(
        confirmed=True,
        confirmation_token=str(issued["confirmation_token"]),
    )

    assert main.service_manager_control("comfyui", "stop", payload, request) == {
        "status": "accepted"
    }
    assert calls == [
        ("POST", "/api/services/comfyui/stop", {"confirmed": True})
    ]


def test_gpu_proxy_exposes_only_bounded_get_paths(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/status"):
            return FakeResponse({"runtime": {}, "jobs": {}})
        return FakeResponse({"ok": True})

    monkeypatch.setattr(main.httpx, "get", fake_get)
    monkeypatch.setattr(main, "_comfyui_bridge_status", lambda: {"state": "unavailable"})
    monkeypatch.setattr(
        main,
        "_generation_ollama_workload",
        lambda: {
            "key": "windows-ollama-generation",
            "state": "idle",
            "models": [],
        },
    )
    job_id = uuid.UUID("f298ae9e-6777-43ee-8462-eb6b95cf18b6")

    assert main.gpu_queue_health() == {"ok": True}
    assert main.gpu_queue_status() == {
        "runtime": {
            "comfyui_bridge": {"state": "unavailable"},
            "external_workloads": [
                {
                    "key": "windows-ollama-generation",
                    "state": "idle",
                    "models": [],
                }
            ],
        },
        "jobs": {},
    }
    assert main.gpu_queue_job(job_id) == {"ok": True}
    assert [call[0] for call in calls] == [
        f"{main.settings.gpu_scheduler_base_url}/api/health",
        f"{main.settings.gpu_scheduler_base_url}/api/status",
        f"{main.settings.gpu_scheduler_base_url}/api/jobs/{job_id}",
    ]
    assert all(call[1]["follow_redirects"] is False for call in calls)


def test_comfyui_bridge_health_is_sanitized_for_gpu_queue_status(monkeypatch):
    monkeypatch.setattr(
        main,
        "_gpu_scheduler_get",
        lambda _path: {"runtime": {"gpu": {}}, "jobs": {}},
    )
    monkeypatch.setattr(
        main,
        "_comfyui_bridge_status",
        lambda: {
            "state": "ready",
            "process_id": 25360,
            "reservation_mode": "prompt_reservation",
            "requested_vram_mb": 26000,
        },
    )
    monkeypatch.setattr(
        main,
        "_generation_ollama_workload",
        lambda: {
            "key": "windows-ollama-generation",
            "state": "idle",
            "models": [],
        },
    )

    result = main.gpu_queue_status()

    assert result["runtime"]["comfyui_bridge"] == {
        "state": "ready",
        "process_id": 25360,
        "reservation_mode": "prompt_reservation",
        "requested_vram_mb": 26000,
    }


def test_gpu_status_does_not_duplicate_scheduler_native_windows_ollama(monkeypatch):
    monkeypatch.setattr(
        main,
        "_gpu_scheduler_get",
        lambda _path: {
            "runtime": {
                "external_workloads": [
                    {"key": "windows-ollama-generation", "state": "active"}
                ]
            },
            "jobs": {},
        },
    )
    monkeypatch.setattr(main, "_comfyui_bridge_status", lambda: {"state": "unavailable"})
    monkeypatch.setattr(
        main,
        "_generation_ollama_workload",
        lambda: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )

    result = main.gpu_queue_status()

    assert len(result["runtime"]["external_workloads"]) == 1


def test_windows_ollama_stop_uses_bounded_local_adapter(monkeypatch):
    expected = {
        "key": "windows-ollama-generation",
        "state": "idle",
        "models": [],
    }
    monkeypatch.setattr(main, "_stop_generation_ollama", lambda: expected)

    assert main.gpu_queue_stop_external_workload("windows-ollama-generation") == {
        "ok": True,
        "workload": expected,
    }


def test_gpu_control_requires_a_server_side_token(monkeypatch):
    monkeypatch.setattr(main.settings, "gpu_scheduler_control_token", "")
    with pytest.raises(main.HTTPException) as exc_info:
        main.gpu_queue_cancel(uuid.uuid4())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["status"] == "control_not_configured"


def test_gpu_control_forwards_only_allowlisted_post_with_server_token(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, **kwargs):
        calls.append((url, kwargs))
        if "/api/external-workloads/" in url:
            return FakeResponse(
                {
                    "ok": True,
                    "workload": {
                        "key": "portal-embedding",
                        "state": "idle",
                        "models": [],
                    },
                }
            )
        return FakeResponse({"ok": True, "queued": [{"id": "first", "manual_rank": 1}]})

    monkeypatch.setattr(main.settings, "gpu_scheduler_control_token", "server-only")
    monkeypatch.setattr(main.httpx, "post", fake_post)
    first = uuid.UUID("f298ae9e-6777-43ee-8462-eb6b95cf18b6")
    second = uuid.UUID("0c95aa2b-c207-4a9e-89e2-fc0651935367")

    assert main.gpu_queue_cancel(first) == {"ok": True, "job_id": str(first)}
    assert main.gpu_queue_stop_external_workload("portal-embedding") == {
        "ok": True,
        "workload": {
            "key": "portal-embedding",
            "state": "idle",
            "models": [],
        },
    }
    assert main.gpu_queue_reorder(main.GpuQueueReorderRequest(job_ids=[first, second])) == {
        "ok": True,
        "queued": [{"id": "first", "manual_rank": 1}],
    }
    assert [call[0] for call in calls] == [
        f"{main.settings.gpu_scheduler_base_url}/api/jobs/{first}/cancel",
        (
            f"{main.settings.gpu_scheduler_base_url}"
            "/api/external-workloads/portal-embedding/stop"
        ),
        f"{main.settings.gpu_scheduler_base_url}/api/jobs/reorder",
    ]
    assert all(call[1]["headers"] == {"X-GPUQ-Token": "server-only"} for call in calls)
    assert all(call[1]["follow_redirects"] is False for call in calls)


def test_gpu_external_stop_rejects_unbounded_workload_keys(monkeypatch):
    monkeypatch.setattr(main.settings, "gpu_scheduler_control_token", "server-only")
    with pytest.raises(main.HTTPException) as exc_info:
        main.gpu_queue_stop_external_workload("../other-container")
    assert exc_info.value.status_code == 422


def test_embedding_recovery_selects_portal_job_without_exposing_command():
    payload = {
        "jobs": {
            "active": [],
            "queued": [
                {
                    "id": "older",
                    "workload_key": "local-knowledge-portal-embedding-reindex",
                    "status": "queued",
                    "submitted_at": "2026-07-25T00:00:00+00:00",
                    "requested_vram_mb": 8192,
                    "argv": ["must", "not", "leak"],
                },
                {
                    "id": "newer",
                    "workload_key": "local-knowledge-portal-embedding-reindex",
                    "status": "queued",
                    "submitted_at": "2026-07-25T01:00:00+00:00",
                    "requested_vram_mb": 8192,
                },
            ],
            "completed": [],
        }
    }

    selected = main._embedding_reindex_job(payload)

    assert selected is not None
    assert selected["id"] == "older"
    assert selected["requested_vram_mb"] == 8192
    assert "argv" not in selected


def test_embedding_recovery_validation_exposes_only_safe_summary(tmp_path):
    snapshot = tmp_path / "embedding-recovery-validation.json"
    snapshot.write_text(
        json.dumps(
            {
                "state": "verified",
                "checked_at": "2026-07-25T00:00:00+00:00",
                "embedding_revision": "revision",
                "anchor_sha256": "internal-only",
                "source_content": "must never reach the API",
                "semantic": {
                    "result_count": 3,
                    "vector_result_count": 3,
                    "best_similarity": 0.99,
                    "provenance_complete": True,
                },
                "hybrid": {
                    "result_count": 3,
                    "vector_result_count": 3,
                    "best_similarity": 0.99,
                    "provenance_complete": True,
                },
            }
        ),
        encoding="utf-8",
    )

    result = main._embedding_recovery_validation(snapshot)

    assert result is not None
    assert result["state"] == "verified"
    assert result["semantic"]["vector_result_count"] == 3
    assert "anchor_sha256" not in result
    assert "source_content" not in result


def test_korean_knowledge_tags_resolve_to_stable_canonical_filters():
    assert main._normalize_knowledge_tag("사례:성능·부하") == "case:performance"
    assert main._normalize_knowledge_tag("작업특성:운영·장애") == "situation:operations"
    assert main._normalize_knowledge_tag("상태:검증됨") == "lifecycle:verified"
    assert (
        main._normalize_knowledge_tag("프로젝트: Local-Knowledge-Portal")
        == "project:local-knowledge-portal"
    )
