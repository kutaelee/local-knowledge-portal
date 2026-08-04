"""Loopback-only allow-listed workstation service controller.

The portal API is the only intended client.  The browser never receives the
control token and callers cannot supply commands, paths, or service metadata.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SERVICE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{1,79}$")
ALLOWED_KINDS = {
    "docker_compose",
    "gpuq_workload",
    "http_process",
    "wsl_systemd_user",
    "read_only",
}
MAX_BODY_BYTES = 1024
MAX_OUTPUT_CHARS = 4000
LOOPBACK_WEB_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_WEB_URLS = {
    "docker:local-knowledge-portal": "http://127.0.0.1:3010/",
    "comfyui": "http://127.0.0.1:8188/",
    "ai-toolkit": "http://127.0.0.1:8675/",
    "gpu-scheduler-host": "http://127.0.0.1:8790/",
}


class ConfigurationError(ValueError):
    pass


class ActionRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    id: str
    label: str
    category: str
    description: str
    kind: str
    control: bool
    warning: str | None
    health_url: str | None
    web_url: str | None
    config: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: object) -> str:
    return str(value or "")[:MAX_OUTPUT_CHARS]


def _validated_web_url(value: object, *, service_id: str) -> str | None:
    if not value:
        return None
    raw = str(value)[:500]
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"invalid web_url for {service_id}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_WEB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ConfigurationError(f"web_url must be a credential-free loopback URL: {service_id}")
    return raw


def load_config(path: Path) -> list[ServiceDefinition]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot read service registry: {type(exc).__name__}") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("services"), list):
        raise ConfigurationError("service registry must use schema_version 1")
    result: list[ServiceDefinition] = []
    seen: set[str] = set()
    for raw in payload["services"]:
        if not isinstance(raw, dict):
            raise ConfigurationError("service registry entries must be objects")
        service_id = str(raw.get("id") or "")
        kind = str(raw.get("kind") or "")
        if not SERVICE_ID.fullmatch(service_id) or service_id in seen:
            raise ConfigurationError(f"invalid or duplicate service id: {service_id!r}")
        if kind not in ALLOWED_KINDS:
            raise ConfigurationError(f"unsupported service kind for {service_id}")
        config = raw.get("config") or {}
        if not isinstance(config, dict):
            raise ConfigurationError(f"config must be an object for {service_id}")
        control = bool(raw.get("control", False))
        if control and kind == "read_only":
            raise ConfigurationError(f"read-only service cannot enable control: {service_id}")
        result.append(
            ServiceDefinition(
                id=service_id,
                label=str(raw.get("label") or service_id)[:120],
                category=str(raw.get("category") or "other")[:40],
                description=str(raw.get("description") or "")[:500],
                kind=kind,
                control=control,
                warning=str(raw["warning"])[:500] if raw.get("warning") else None,
                health_url=str(raw["health_url"]) if raw.get("health_url") else None,
                web_url=_validated_web_url(raw.get("web_url"), service_id=service_id),
                config=config,
            )
        )
        seen.add(service_id)
    return result


def _run(
    argv: list[str],
    *,
    timeout: float = 60,
    cwd: str | None = None,
    capture_output: bool = True,
) -> dict[str, Any]:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ConfigurationError("configured argv must contain non-empty strings")
    output_options = (
        {"capture_output": True}
        if capture_output
        else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    )
    completed = subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        shell=False,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        **output_options,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": _bounded(completed.stdout),
        "stderr": _bounded(completed.stderr),
    }


def _http_probe(url: str, *, timeout: float = 2.0) -> tuple[str, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        return "error", f"HTTP {exc.code}"
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return "offline", type(exc).__name__
    return ("healthy", f"HTTP {status}") if 200 <= status < 400 else ("error", f"HTTP {status}")


def _docker_argv(service: ServiceDefinition, action: str | None = None) -> list[str]:
    config_file = str(service.config.get("config_file") or "")
    runner = str(service.config.get("runner") or "")
    if not config_file or runner not in {"windows", "wsl"}:
        raise ConfigurationError(f"docker compose boundary is incomplete for {service.id}")
    compose: list[str]
    if runner == "wsl":
        distro = str(service.config.get("distro") or "Ubuntu")
        compose = ["wsl.exe", "-d", distro, "--", "docker", "compose"]
    else:
        compose = ["docker", "compose"]
    env_file = service.config.get("env_file")
    if env_file:
        compose.extend(["--env-file", str(env_file)])
    compose.extend(["-f", config_file])
    project = service.config.get("project")
    if project:
        compose.extend(["--project-name", str(project)])
    if action == "start":
        compose.extend(["up", "-d"])
    elif action == "stop":
        compose.append("stop")
    services = service.config.get("services") or []
    if not isinstance(services, list) or any(not isinstance(item, str) for item in services):
        raise ConfigurationError(f"invalid compose services for {service.id}")
    if action:
        compose.extend(services)
    return compose


def _docker_status(service: ServiceDefinition) -> tuple[str, str, list[dict[str, str]]]:
    project = str(service.config.get("project") or "")
    if not project:
        raise ConfigurationError(f"docker project is required for {service.id}")
    argv = [
        "docker",
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{json .}}",
    ]
    result = _run(argv, timeout=15)
    if result["exit_code"] != 0:
        return "error", result["stderr"] or "docker inventory failed", []
    selected = set(service.config.get("services") or [])
    rows: list[dict[str, str]] = []
    for line in result["stdout"].splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        service_name = str(item.get("Labels") or "")
        label_match = re.search(r"(?:^|,)com\.docker\.compose\.service=([^,]+)", service_name)
        compose_service = label_match.group(1) if label_match else ""
        if selected and compose_service not in selected:
            continue
        rows.append(
            {
                "name": str(item.get("Names") or ""),
                "service": compose_service,
                "state": str(item.get("State") or "").lower(),
                "status": str(item.get("Status") or ""),
            }
        )
    if not rows:
        return "offline", "등록된 컨테이너가 아직 생성되지 않았습니다.", []
    running = [item for item in rows if item["state"] == "running"]
    if len(running) == len(rows):
        return "healthy", f"{len(running)}개 컨테이너 실행 중", rows
    if running:
        return "degraded", f"{len(running)}/{len(rows)}개 컨테이너 실행 중", rows
    return "offline", "모든 컨테이너가 중지되었습니다.", rows


def _wsl_systemd_status(service: ServiceDefinition) -> tuple[str, str]:
    distro = str(service.config.get("distro") or "Ubuntu")
    unit = str(service.config.get("unit") or "")
    if not unit:
        raise ConfigurationError(f"systemd unit is required for {service.id}")
    result = _run(
        ["wsl.exe", "-d", distro, "--", "systemctl", "--user", "is-active", unit],
        timeout=15,
    )
    state = result["stdout"].strip()
    if result["exit_code"] == 0 and state == "active":
        return "healthy", "systemd user service active"
    if state in {"inactive", "failed", "deactivating"}:
        return "offline" if state == "inactive" else "error", state
    return "offline", state or result["stderr"] or "not running"


def _gpuq_job(service: ServiceDefinition) -> dict[str, Any] | None:
    scheduler_url = str(
        service.config.get("scheduler_url") or "http://127.0.0.1:8790/api/status"
    )
    workload_key = str(service.config.get("workload_key") or "")
    if not workload_key:
        raise ConfigurationError(f"workload_key is required for {service.id}")
    try:
        with urllib.request.urlopen(scheduler_url, timeout=2.0) as response:
            payload = json.loads(response.read(1_000_000))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, dict):
        return None
    for bucket in ("active", "queued"):
        rows = jobs.get(bucket)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("workload_key") == workload_key:
                return {**row, "bucket": bucket}
    return None


def service_status(service: ServiceDefinition) -> dict[str, Any]:
    components: list[dict[str, str]] = []
    try:
        if service.kind == "docker_compose":
            state, detail, components = _docker_status(service)
        elif service.kind == "wsl_systemd_user":
            state, detail = _wsl_systemd_status(service)
        elif service.kind == "gpuq_workload":
            job = _gpuq_job(service)
            if job:
                state = "healthy" if job["bucket"] == "active" else "pending"
                detail = (
                    "GPU 예약에서 실행 중"
                    if job["bucket"] == "active"
                    else "GPU 실행 순서를 기다리는 중"
                )
                components = [
                    {
                        "name": str(job.get("id") or ""),
                        "service": str(job.get("workload_key") or ""),
                        "state": str(job.get("status") or job["bucket"]),
                        "status": str(job.get("scheduling_note") or ""),
                    }
                ]
            elif service.health_url:
                state, detail = _http_probe(service.health_url)
                if state == "healthy":
                    state = "unmanaged"
                    detail = "GPU 큐 외부에서 실행 중"
            else:
                state, detail = "offline", "GPU 예약 작업이 없습니다."
        elif service.health_url:
            state, detail = _http_probe(service.health_url)
        else:
            state, detail = "unknown", "health probe is not configured"
    except (ConfigurationError, OSError, subprocess.SubprocessError) as exc:
        state, detail = "error", type(exc).__name__
    return {
        "id": service.id,
        "label": service.label,
        "category": service.category,
        "description": service.description,
        "state": state,
        "detail": detail,
        "can_start": service.control and state not in {"healthy", "running", "unmanaged"},
        "can_stop": service.control
        and state in {"healthy", "running", "degraded", "pending", "unmanaged"},
        "warning": service.warning,
        "web_url": service.web_url or DEFAULT_WEB_URLS.get(service.id),
        "components": components,
    }


def _guard_stop(service: ServiceDefinition) -> None:
    guard = str(service.config.get("stop_guard") or "")
    if guard != "comfyui_queue_empty":
        return
    queue_url = str(service.config.get("queue_url") or "")
    if not queue_url:
        raise ConfigurationError("ComfyUI stop guard requires queue_url")
    try:
        with urllib.request.urlopen(queue_url, timeout=2.0) as response:
            payload = json.loads(response.read(MAX_BODY_BYTES * 8))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ActionRejected("ComfyUI 작업 대기열을 확인하지 못해 중지를 거부했습니다.") from exc
    if payload.get("queue_running") or payload.get("queue_pending"):
        raise ActionRejected("ComfyUI에서 실행 또는 대기 중인 작업이 있어 중지를 거부했습니다.")


def _configured_argv(service: ServiceDefinition, action: str) -> tuple[list[str], str | None]:
    argv = service.config.get(f"{action}_argv")
    if not isinstance(argv, list):
        raise ConfigurationError(f"{action}_argv is required for {service.id}")
    cwd = str(service.config["cwd"]) if service.config.get("cwd") else None
    return [str(item) for item in argv], cwd


def control_service(service: ServiceDefinition, action: str) -> dict[str, Any]:
    if action not in {"start", "stop"} or not service.control:
        raise ActionRejected("이 서비스는 포털 제어 대상으로 등록되지 않았습니다.")
    before = service_status(service)
    if action == "start" and before["state"] in {"healthy", "running", "pending"}:
        return {"status": "unchanged", "action": action, "service": before}
    if action == "stop" and before["state"] not in {
        "healthy",
        "running",
        "degraded",
        "pending",
        "unmanaged",
    }:
        return {"status": "unchanged", "action": action, "service": before}
    if action == "stop":
        _guard_stop(service)
    if service.kind == "docker_compose":
        argv, cwd = _docker_argv(service, action), None
    elif service.kind == "gpuq_workload" and action == "stop":
        job = _gpuq_job(service)
        if job is not None and job.get("id"):
            argv = ["gpuq", "cancel", str(job["id"])]
            cwd = None
        else:
            argv = service.config.get("unmanaged_stop_argv")
            if not isinstance(argv, list):
                raise ActionRejected("중지할 GPU 예약 작업을 찾지 못했습니다.")
            argv = [str(item) for item in argv]
            cwd = None
    elif service.kind == "wsl_systemd_user":
        distro = str(service.config.get("distro") or "Ubuntu")
        unit = str(service.config.get("unit") or "")
        argv = [
            "wsl.exe",
            "-d",
            distro,
            "--",
            "systemctl",
            "--user",
            action,
            unit,
        ]
        cwd = None
    else:
        argv, cwd = _configured_argv(service, action)
    execution = _run(
        argv,
        timeout=float(service.config.get("action_timeout_seconds") or 90),
        cwd=cwd,
        capture_output=not (
            service.kind == "http_process"
            and action == "start"
            and bool(service.config.get("detached_child"))
        ),
    )
    if execution["exit_code"] != 0:
        raise ActionRejected(
            execution["stderr"] or execution["stdout"] or f"{action} command failed"
        )
    after = service_status(service)
    return {
        "status": "accepted",
        "action": action,
        "execution": {"exit_code": execution["exit_code"]},
        "service": after,
    }


class ServiceManager:
    def __init__(self, config_path: Path, token: str):
        if len(token) < 32:
            raise ConfigurationError("LKP_SERVICE_MANAGER_TOKEN must be at least 32 characters")
        self.config_path = config_path
        self.token = token
        self.locks: dict[str, threading.Lock] = {}

    def definitions(self) -> list[ServiceDefinition]:
        return load_config(self.config_path)

    def status(self) -> dict[str, Any]:
        services = [service_status(item) for item in self.definitions()]
        return {
            "status": "ready",
            "checked_at": _utc_now(),
            "services": services,
            "summary": {
                "healthy": sum(item["state"] == "healthy" for item in services),
                "attention": sum(
                    item["state"] not in {"healthy", "running"} for item in services
                ),
                "controllable": sum(item["can_start"] or item["can_stop"] for item in services),
            },
        }

    def control(self, service_id: str, action: str) -> dict[str, Any]:
        definitions = {item.id: item for item in self.definitions()}
        service = definitions.get(service_id)
        if service is None:
            raise KeyError(service_id)
        lock = self.locks.setdefault(service_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ActionRejected("이 서비스의 다른 제어 작업이 진행 중입니다.")
        try:
            return control_service(service, action)
        finally:
            lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LkpServiceManager/1"

    @property
    def manager(self) -> ServiceManager:
        return self.server.manager  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        print(
            json.dumps(
                {
                    "timestamp": _utc_now(),
                    "service": "host-service-manager",
                    "event": "http_request",
                    "message": format % args,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _write(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.manager.token}"
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, expected)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._write(
                HTTPStatus.OK,
                {"status": "healthy", "capabilities": ["loopback_web_url"]},
            )
            return
        if self.path != "/api/services":
            self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._authorized():
            self._write(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            self._write(HTTPStatus.OK, self.manager.status())
        except ConfigurationError as exc:
            self._write(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "configuration_error", "detail": _bounded(exc)},
            )

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        match = re.fullmatch(r"/api/services/([^/]+)/(start|stop)", self.path)
        if not match or not SERVICE_ID.fullmatch(match.group(1)):
            self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = MAX_BODY_BYTES + 1
        if length > MAX_BODY_BYTES:
            self._write(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "payload_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._write(HTTPStatus.BAD_REQUEST, {"error": "malformed_json"})
            return
        if payload != {"confirmed": True}:
            self._write(HTTPStatus.BAD_REQUEST, {"error": "explicit_confirmation_required"})
            return
        try:
            result = self.manager.control(match.group(1), match.group(2))
        except KeyError:
            self._write(HTTPStatus.NOT_FOUND, {"error": "unknown_service"})
        except (ActionRejected, ConfigurationError, subprocess.SubprocessError) as exc:
            self._write(
                HTTPStatus.CONFLICT,
                {"error": "action_rejected", "detail": _bounded(exc)},
            )
        else:
            self._write(HTTPStatus.OK, result)


def main() -> None:
    config_path = Path(
        os.environ.get(
            "LKP_SERVICE_MANAGER_CONFIG",
            r"C:\Docker\local-knowledge-portal\config\managed-services.json",
        )
    )
    token = os.environ.get("LKP_SERVICE_MANAGER_TOKEN", "")
    manager = ServiceManager(config_path, token)
    host = os.environ.get("LKP_SERVICE_MANAGER_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("host service manager must remain loopback-only")
    port = int(os.environ.get("LKP_SERVICE_MANAGER_PORT", "8791"))
    server = ThreadingHTTPServer((host, port), Handler)
    server.manager = manager  # type: ignore[attr-defined]
    print(
        json.dumps(
            {
                "timestamp": _utc_now(),
                "service": "host-service-manager",
                "event": "started",
                "host": host,
                "port": port,
                "registered_services": len(manager.definitions()),
            }
        ),
        flush=True,
    )
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
