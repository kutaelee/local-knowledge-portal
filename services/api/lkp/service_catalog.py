from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PORTAL_PROJECT = "local-knowledge-portal"
SHARED_PROJECTS = {
    "gpu-workload-scheduler",
    "workstation-databases",
    "workstation-edge-ingress",
}


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _container_state(container: dict[str, Any], *, stale: bool) -> tuple[str, str]:
    if stale:
        return "stale", "inventory snapshot is stale"
    health = str(container.get("health") or "").lower()
    state = str(container.get("state") or "").lower()
    if health == "healthy":
        return "healthy", "Docker healthcheck passed"
    if health == "unhealthy":
        return "error", "Docker healthcheck failed"
    if health == "starting":
        return "stale", "Docker healthcheck is starting"
    if state == "running":
        return "running", "running; no Docker healthcheck"
    return "offline", str(container.get("status") or state or "not running")


def _category(project: str, working_dir: str) -> str:
    if project == PORTAL_PROJECT:
        return "portal"
    if project in SHARED_PROJECTS or project.startswith("workstation-"):
        return "infrastructure"
    normalized = working_dir.replace("\\", "/").lower()
    if normalized.startswith("c:/docker/compose/"):
        return "infrastructure"
    return "projects"


def load_docker_groups(
    path: Path,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inventory: dict[str, Any] = {
        "state": "offline",
        "checked_at": None,
        "age_seconds": None,
        "detail": "Docker inventory snapshot is not available",
    }
    if not path.is_file():
        return inventory, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        inventory["state"] = "error"
        inventory["detail"] = "Docker inventory snapshot is malformed"
        return inventory, []
    if payload.get("schema_version") != 1 or not isinstance(payload.get("containers"), list):
        inventory["state"] = "error"
        inventory["detail"] = "Docker inventory schema is unsupported"
        return inventory, []

    checked_at = _timestamp(payload.get("checked_at"))
    age_seconds = None if checked_at is None else max(0, int((now - checked_at).total_seconds()))
    stale = checked_at is None or age_seconds > stale_after_seconds
    collector_error = payload.get("error")
    inventory.update(
        {
            "state": "error" if collector_error else "stale" if stale else "healthy",
            "checked_at": checked_at,
            "age_seconds": age_seconds,
            "detail": str(collector_error)
            if collector_error
            else f"{len(payload['containers'])} running container(s)",
        }
    )

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in payload["containers"]:
        if not isinstance(item, dict):
            continue
        project = str(item.get("project") or "standalone")
        if project == PORTAL_PROJECT:
            continue
        service = str(item.get("service") or item.get("name") or "container")
        working_dir = str(item.get("working_dir") or "")
        category = _category(project, working_dir)
        state, state_detail = _container_state(item, stale=stale)
        key = (category, project)
        group = grouped.setdefault(
            key,
            {
                "key": f"{category}:{project}",
                "category": category,
                "project": project,
                "services": [],
            },
        )
        ports = str(item.get("ports") or "")
        detail = state_detail if not ports else f"{state_detail} · {ports}"
        group["services"].append(
            {
                "key": f"docker:{project}:{service}",
                "label": service,
                "state": state,
                "detail": detail,
                "project": project,
                "service": service,
                "container": item.get("name"),
                "image": item.get("image"),
                "ports": ports,
                "verification": "healthcheck"
                if item.get("health")
                else "running_only",
            }
        )

    category_order = {"projects": 0, "infrastructure": 1}
    groups = sorted(
        grouped.values(),
        key=lambda group: (
            category_order.get(group["category"], 9),
            group["project"].casefold(),
        ),
    )
    for group in groups:
        group["services"].sort(key=lambda service: service["service"].casefold())
        states = {service["state"] for service in group["services"]}
        group["state"] = (
            "error"
            if "error" in states or "offline" in states
            else "stale"
            if "stale" in states
            else "running"
            if "running" in states
            else "healthy"
        )
    return inventory, groups
