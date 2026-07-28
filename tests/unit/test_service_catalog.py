import json
from datetime import datetime, timedelta, timezone

from lkp.service_catalog import load_docker_groups, load_gpu_embedding_reaper


def _write_snapshot(path, *, checked_at: datetime, containers: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checked_at": checked_at.isoformat(),
                "containers": containers,
                "error": None,
            }
        ),
        encoding="utf-8",
    )


def test_groups_project_services_and_excludes_portal_duplicates(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "docker.json"
    _write_snapshot(
        snapshot,
        checked_at=now,
        containers=[
            {
                "name": "mining-app",
                "project": "unjeong-mining-web",
                "service": "app",
                "state": "running",
                "health": "healthy",
                "ports": "127.0.0.1:31234->31234/tcp",
                "working_dir": "/home/kutae/src/unjeong-mining-web-dev",
            },
            {
                "name": "lkp-web",
                "project": "local-knowledge-portal",
                "service": "web",
                "state": "running",
                "health": "healthy",
            },
        ],
    )

    inventory, groups = load_docker_groups(
        snapshot,
        now=now,
        stale_after_seconds=90,
    )

    assert inventory["state"] == "healthy"
    assert [group["project"] for group in groups] == ["unjeong-mining-web"]
    assert groups[0]["category"] == "projects"
    assert groups[0]["services"][0]["state"] == "healthy"


def test_marks_running_without_healthcheck_as_unverified(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "docker.json"
    _write_snapshot(
        snapshot,
        checked_at=now,
        containers=[
            {
                "name": "tunnel",
                "project": "workstation-edge-ingress",
                "service": "cloudflared",
                "state": "running",
                "health": "",
                "working_dir": r"C:\Docker\Compose\edge-ingress",
            }
        ],
    )

    _, groups = load_docker_groups(snapshot, now=now, stale_after_seconds=90)

    assert groups[0]["category"] == "infrastructure"
    assert groups[0]["services"][0]["state"] == "running"
    assert groups[0]["services"][0]["verification"] == "running_only"


def test_stale_snapshot_does_not_report_healthy_containers(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "docker.json"
    _write_snapshot(
        snapshot,
        checked_at=now - timedelta(minutes=5),
        containers=[
            {
                "name": "app",
                "project": "example",
                "service": "app",
                "state": "running",
                "health": "healthy",
            }
        ],
    )

    inventory, groups = load_docker_groups(
        snapshot,
        now=now,
        stale_after_seconds=90,
    )

    assert inventory["state"] == "stale"
    assert groups[0]["services"][0]["state"] == "stale"


def test_gpu_embedding_reaper_requires_a_fresh_scheduler_confirmed_snapshot(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "gpu-embedding-reaper.json"
    snapshot.write_text(
        json.dumps(
            {
                "checked_at": now.isoformat(),
                "state": "no_batch_container",
                "scheduler_ok": True,
                "action": "none",
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    result = load_gpu_embedding_reaper(snapshot, now=now, stale_after_seconds=90)

    assert result["state"] == "healthy"
    assert "temporary GPU embedding batch" in result["detail"]


def test_gpu_embedding_reaper_fails_closed_for_stale_or_malformed_status(tmp_path):
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "gpu-embedding-reaper.json"
    snapshot.write_text("{not json", encoding="utf-8")
    assert load_gpu_embedding_reaper(snapshot, now=now, stale_after_seconds=90)["state"] == "error"

    snapshot.write_text(
        json.dumps(
            {
                "checked_at": (now - timedelta(minutes=3)).isoformat(),
                "state": "no_batch_container",
                "scheduler_ok": True,
            }
        ),
        encoding="utf-8",
    )
    assert load_gpu_embedding_reaper(snapshot, now=now, stale_after_seconds=90)["state"] == "stale"

    snapshot.write_text(
        json.dumps(
            {
                "checked_at": now.isoformat(),
                "state": "unrecognized_future_state",
                "scheduler_ok": True,
            }
        ),
        encoding="utf-8",
    )
    assert load_gpu_embedding_reaper(snapshot, now=now, stale_after_seconds=90)["state"] == "error"
