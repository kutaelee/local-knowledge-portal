import uuid

import pytest
from lkp import main
from lkp.settings import Settings
from pydantic import ValidationError


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


def test_gpu_proxy_exposes_only_bounded_get_paths(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"ok": True})

    monkeypatch.setattr(main.httpx, "get", fake_get)
    job_id = uuid.UUID("f298ae9e-6777-43ee-8462-eb6b95cf18b6")

    assert main.gpu_queue_health() == {"ok": True}
    assert main.gpu_queue_status() == {"ok": True}
    assert main.gpu_queue_job(job_id) == {"ok": True}
    assert [call[0] for call in calls] == [
        f"{main.settings.gpu_scheduler_base_url}/api/health",
        f"{main.settings.gpu_scheduler_base_url}/api/status",
        f"{main.settings.gpu_scheduler_base_url}/api/jobs/{job_id}",
    ]
    assert all(call[1]["follow_redirects"] is False for call in calls)
