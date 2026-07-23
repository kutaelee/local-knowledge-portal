from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from lkp.db import SessionLocal
from lkp.models import GeneratedPage, JobStatus
from lkp.settings import Settings, get_settings
from sqlalchemy import select

from .generation import GenerationProvider, GenerationResult, build_generation_provider
from .paths import idempotency_key
from .queue import enqueue
from .scanner import register_roots
from .worker import process_job

GENERATOR = "local-knowledge-portal"
CAPTURE_PIPELINE_VERSION = "codex-transcript-v1"
MANAGED_PREFIX = "_generated/codex-sessions"
ENRICHMENT_PREFIX = "_generated/codex-summaries"
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\b"
    r"(\s*[:=]\s*)([\"']?)([^\s\"']{8,})([\"']?)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_INJECTED_USER_PREFIXES = (
    "<recommended_plugins>",
    "# AGENTS.md instructions",
    "<environment_context>",
    "<permissions instructions>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<skills_instructions>",
)


@dataclass(slots=True)
class Message:
    role: str
    text: str
    timestamp: str | None = None
    phase: str | None = None


@dataclass(slots=True)
class Transcript:
    session_id: str
    started_at: str
    updated_at: str
    workspace: str
    originator: str
    cli_version: str
    messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class SyncResult:
    session_id: str
    output_path: Path
    message_count: int
    source_hash: str
    changed: bool
    indexed: bool = False


def redact_text(value: str) -> str:
    value = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value
    )
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _OPENAI_KEY.sub("[REDACTED API KEY]", value)


def _is_injected_user_context(value: str) -> bool:
    stripped = value.lstrip()
    return any(stripped.startswith(prefix) for prefix in _INJECTED_USER_PREFIXES)


def _message_texts(payload: dict[str, Any]) -> list[str]:
    result: list[str] = []
    role = payload.get("role")
    for part in payload.get("content") or []:
        if not isinstance(part, dict) or part.get("type") not in {"input_text", "output_text"}:
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and _is_injected_user_context(text):
            continue
        result.append(text.strip())
    return result


def parse_transcript(path: Path) -> Transcript:
    session_id = ""
    started_at = ""
    workspace = ""
    originator = "Codex"
    cli_version = ""
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    messages: list[Message] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            timestamp = event.get("timestamp")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("type") == "session_meta":
                session_id = str(payload.get("session_id") or payload.get("id") or "")
                started_at = str(payload.get("timestamp") or timestamp or "")
                workspace = Path(str(payload.get("cwd") or "")).name
                originator = str(payload.get("originator") or originator)
                cli_version = str(payload.get("cli_version") or "")
                continue
            if (
                event.get("type") != "response_item"
                or payload.get("type") != "message"
                or payload.get("role") not in {"user", "assistant"}
            ):
                continue
            texts = _message_texts(payload)
            if texts:
                messages.append(
                    Message(
                        role=str(payload["role"]),
                        text=redact_text("\n\n".join(texts)),
                        timestamp=str(timestamp) if timestamp else None,
                        phase=str(payload.get("phase")) if payload.get("phase") else None,
                    )
                )

    if not session_id:
        match = re.search(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", path.name)
        fallback_id = hashlib.sha256(str(path).encode()).hexdigest()[:32]
        session_id = match.group(1) if match else fallback_id
    if not started_at:
        started_at = datetime.fromtimestamp(path.stat().st_ctime, timezone.utc).isoformat()
    return Transcript(
        session_id=session_id,
        started_at=started_at,
        updated_at=updated_at,
        workspace=workspace,
        originator=originator,
        cli_version=cli_version,
        messages=messages,
    )


def _source_hash(transcript: Transcript) -> str:
    payload = {
        "session_id": transcript.session_id,
        "started_at": transcript.started_at,
        "messages": [
            {"role": item.role, "text": item.text, "timestamp": item.timestamp, "phase": item.phase}
            for item in transcript.messages
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _date_parts(started_at: str) -> tuple[str, str, str]:
    try:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed.strftime("%Y"), parsed.strftime("%m"), parsed.strftime("%d")


def output_path_for(vault_dir: Path, transcript: Transcript) -> Path:
    year, month, day = _date_parts(transcript.started_at)
    safe_id = re.sub(r"[^0-9A-Za-z-]", "-", transcript.session_id)
    target = vault_dir / MANAGED_PREFIX / year / month / f"{day}-{safe_id}.md"
    vault_resolved = vault_dir.resolve()
    target_resolved = target.resolve(strict=False)
    if not target_resolved.is_relative_to(vault_resolved):
        raise ValueError("managed output escaped the configured vault")
    return target_resolved


def enrichment_path_for(vault_dir: Path, transcript: Transcript) -> Path:
    year, month, day = _date_parts(transcript.started_at)
    safe_id = re.sub(r"[^0-9A-Za-z-]", "-", transcript.session_id)
    target = vault_dir / ENRICHMENT_PREFIX / year / month / f"{day}-{safe_id}.md"
    vault_resolved = vault_dir.resolve()
    target_resolved = target.resolve(strict=False)
    if not target_resolved.is_relative_to(vault_resolved):
        raise ValueError("managed enrichment output escaped the configured vault")
    return target_resolved


def _render(transcript: Transcript, source_hash: str, generated_at: str) -> str:
    metadata = {
        "managed": True,
        "generator": GENERATOR,
        "source_ids": [f"codex:{transcript.session_id}"],
        "source_hashes": [source_hash],
        "pipeline_version": CAPTURE_PIPELINE_VERSION,
        "generated_at": generated_at,
        "page_type": "codex-work-session",
        "session_id": transcript.session_id,
        "started_at": transcript.started_at,
        "updated_at": transcript.updated_at,
        "tags": ["codex", "work-session", "generated"],
    }
    lines = [
        "---",
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# Codex 작업 세션 {transcript.session_id[:8]}",
        "",
        "> [!note] 관측된 기록",
        "> Codex transcript에서 사용자와 어시스턴트의 표시 메시지만 추출했습니다. "
        "시스템 지시, 내부 reasoning, 도구 입출력은 포함하지 않습니다.",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 시작 | {transcript.started_at} |",
        f"| 최근 반영 | {transcript.updated_at} |",
        f"| 작업공간 | {transcript.workspace or '-'} |",
        f"| 클라이언트 | {transcript.originator} {transcript.cli_version} |",
        f"| 메시지 수 | {len(transcript.messages)} |",
        "",
    ]
    for index, message in enumerate(transcript.messages, 1):
        label = "사용자 작성 정보" if message.role == "user" else "Codex 응답"
        phase = f" · {message.phase}" if message.phase else ""
        lines.extend(
            [
                f"## {index}. {label}{phase}",
                "",
                f"_기록 시각: {message.timestamp or 'unknown'}_",
                "",
                message.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _existing_source_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    post = frontmatter.load(path)
    if post.get("managed") is not True or post.get("generator") != GENERATOR:
        raise PermissionError(f"refusing to overwrite non-managed page: {path}")
    hashes = post.get("source_hashes") or []
    return str(hashes[0]) if hashes else None


def write_managed_page(transcript_path: Path, vault_dir: Path) -> SyncResult:
    transcript_path = transcript_path.resolve(strict=True)
    transcript = parse_transcript(transcript_path)
    source_hash = _source_hash(transcript)
    output_path = output_path_for(vault_dir, transcript)
    if _existing_source_hash(output_path) == source_hash:
        return SyncResult(
            transcript.session_id, output_path, len(transcript.messages), source_hash, False
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render(transcript, source_hash, datetime.now(timezone.utc).isoformat())
    temporary = output_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, output_path)
    return SyncResult(
        transcript.session_id, output_path, len(transcript.messages), source_hash, True
    )


def _generation_source_text(transcript: Transcript, max_chars: int) -> str:
    selected: list[str] = []
    used = 0
    for message in reversed(transcript.messages):
        label = "USER" if message.role == "user" else "ASSISTANT"
        rendered = f"[{label}]\n{message.text}"
        if selected and used + len(rendered) > max_chars:
            break
        if len(rendered) > max_chars:
            rendered = rendered[-max_chars:]
        selected.append(rendered)
        used += len(rendered)
    selected.reverse()
    prefix = "[Earlier messages omitted deterministically]\n\n" if len(selected) < len(
        transcript.messages
    ) else ""
    return prefix + "\n\n".join(selected)


def _write_enrichment_page(
    transcript: Transcript,
    source_hash: str,
    generated: GenerationResult,
    vault_dir: Path,
) -> SyncResult:
    output_path = enrichment_path_for(vault_dir, transcript)
    if output_path.exists():
        post = frontmatter.load(output_path)
        if post.get("managed") is not True or post.get("generator") != GENERATOR:
            raise PermissionError(f"refusing to overwrite non-managed page: {output_path}")
        if (
            (post.get("source_hashes") or [None])[0] == source_hash
            and post.get("generation_provider") == generated.provider
            and post.get("generation_model") == generated.model
            and post.get("generation_model_digest") == generated.model_digest
        ):
            return SyncResult(
                transcript.session_id,
                output_path,
                len(transcript.messages),
                source_hash,
                False,
            )
    metadata = {
        "managed": True,
        "generator": GENERATOR,
        "source_ids": [f"codex:{transcript.session_id}"],
        "source_hashes": [source_hash],
        "pipeline_version": "codex-enrichment-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "page_type": "codex-work-session-enrichment",
        "generation_provider": generated.provider,
        "generation_model": generated.model,
        "generation_model_digest": generated.model_digest,
        "tags": ["codex", "generated", "llm-enrichment"],
    }
    content = generated.content
    lines = [
        "---",
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# Codex 세션 요약 {transcript.session_id[:8]}",
        "",
        "> [!warning] LLM 생성 정보",
        "> 이 문서는 로컬 모델이 생성했습니다. 원본 대화 문서와 구분되며 확인이 필요합니다.",
        "",
        "## LLM 생성 요약",
        "",
        content.summary,
        "",
        "## 관측된 사실",
        "",
        *([f"- {item}" for item in content.observed_facts] or ["- 없음"]),
        "",
        "## 파일에서 추출한 정보",
        "",
        *([f"- {item}" for item in content.extracted_information] or ["- 없음"]),
        "",
        "## 확인이 필요한 추론",
        "",
        *([f"- {item}" for item in content.inferences_needing_confirmation] or ["- 없음"]),
        "",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    os.replace(temporary, output_path)
    return SyncResult(
        transcript.session_id, output_path, len(transcript.messages), source_hash, True
    )


def enrich_transcript(
    transcript_path: Path,
    settings: Settings,
    provider: GenerationProvider,
) -> SyncResult:
    transcript = parse_transcript(transcript_path.resolve(strict=True))
    source_hash = _source_hash(transcript)
    generated = provider.generate(
        _generation_source_text(transcript, settings.generation_max_input_chars)
    )
    return _write_enrichment_page(transcript, source_hash, generated, settings.vault_dir)


def index_managed_page(result: SyncResult, settings: Settings) -> bool:
    with SessionLocal() as session:
        roots = register_roots(session, settings.source_roots_config)
        output = result.output_path.resolve(strict=True)
        root = next(
            (
                item
                for item in roots
                if output.is_relative_to(Path(item.canonical_path).resolve(strict=True))
            ),
            None,
        )
        if root is None:
            raise RuntimeError("managed vault is not registered as a source root")
        info = output.stat()
        key = idempotency_key(str(root.id), str(output), info.st_size, info.st_mtime_ns)
        job = enqueue(
            session,
            key=key,
            source_root_id=root.id,
            canonical_path=str(output),
            job_type="codex_capture",
            max_attempts=settings.max_attempts,
        )
        if job is None:
            session.commit()
            return False
        worker_id = f"codex-capture-{socket.gethostname()}-{os.getpid()}"
        job.status = JobStatus.leased
        job.leased_by = worker_id
        job.attempt_count = 1
        session.flush()
        process_job(session, job, settings, None, worker_id)
        existing = session.scalar(
            select(GeneratedPage).where(
                GeneratedPage.relative_path
                == output.relative_to(settings.vault_dir.resolve()).as_posix()
            )
        )
        if existing is None:
            session.add(
                GeneratedPage(
                    relative_path=output.relative_to(settings.vault_dir.resolve()).as_posix(),
                    source_hashes=[result.source_hash],
                    pipeline_version=CAPTURE_PIPELINE_VERSION,
                )
            )
        else:
            existing.source_hashes = [result.source_hash]
            existing.pipeline_version = CAPTURE_PIPELINE_VERSION
            existing.generated_at = datetime.now(timezone.utc)
        session.commit()
        result.indexed = True
        return True


def _log(settings: Settings, event: str, **fields: Any) -> None:
    log_dir = settings.runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "error" if "error" in fields else "info",
        "service": "codex-capture",
        "event": event,
        **{key: str(value) for key, value in fields.items()},
    }
    with (log_dir / "codex-capture.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def sync_one(
    path: Path,
    settings: Settings,
    *,
    index: bool,
    generation_provider: GenerationProvider | None = None,
) -> SyncResult:
    result = write_managed_page(path, settings.vault_dir)
    if index and result.changed:
        try:
            index_managed_page(result, settings)
        except Exception as exc:
            _log(settings, "index_failed", path=path, error=type(exc).__name__, detail=str(exc))
    if generation_provider:
        transcript = parse_transcript(path.resolve(strict=True))
        enrichment_path = enrichment_path_for(settings.vault_dir, transcript)
        if result.changed or not enrichment_path.exists():
            try:
                enrichment = enrich_transcript(path, settings, generation_provider)
                if index and enrichment.changed:
                    index_managed_page(enrichment, settings)
            except Exception as exc:
                _log(
                    settings,
                    "generation_failed",
                    path=path,
                    error=type(exc).__name__,
                    detail=str(exc),
                )
    return result


def _transcripts(codex_homes: list[Path]) -> list[Path]:
    files = [
        transcript
        for home in codex_homes
        for transcript in (home / "sessions").glob("**/*.jsonl")
    ]
    return sorted(files, key=lambda item: item.stat().st_mtime_ns)


def watch(
    settings: Settings,
    codex_homes: list[Path],
    poll_seconds: float,
    *,
    index: bool,
    generation_provider: GenerationProvider | None,
    import_existing: bool,
) -> None:
    files = _transcripts(codex_homes)
    known = {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in files
    }
    if import_existing:
        for path in files:
            sync_one(
                path,
                settings,
                index=index,
                generation_provider=generation_provider,
            )
    elif files:
        latest = files[-1]
        sync_one(
            latest,
            settings,
            index=index,
            generation_provider=generation_provider,
        )
    _log(
        settings,
        "watch_started",
        codex_homes=";".join(str(path) for path in codex_homes),
        initial_files=len(files),
        generation_provider=settings.generation_provider,
    )
    while True:
        for path in _transcripts(codex_homes):
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if known.get(path) == signature:
                continue
            try:
                result = sync_one(
                    path,
                    settings,
                    index=index,
                    generation_provider=generation_provider,
                )
                known[path] = signature
                if result.changed:
                    _log(
                        settings,
                        "session_synced",
                        session_id=result.session_id,
                        output_path=result.output_path,
                        messages=result.message_count,
                        indexed=result.indexed,
                    )
            except Exception as exc:
                _log(
                    settings,
                    "sync_failed",
                    path=path,
                    error=type(exc).__name__,
                    detail=str(exc),
                )
        time.sleep(max(1.0, poll_seconds))


def _hook_path() -> Path | None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return None
    value = payload.get("transcript_path")
    return Path(value) if isinstance(value, str) and value else None


def main() -> int:
    parser = argparse.ArgumentParser(prog="lkp-codex-capture")
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--hook-stdin", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--import-existing", action="store_true")
    parser.add_argument("--codex-home", type=Path, action="append")
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    generation_provider = build_generation_provider(settings) if args.enrich else None
    try:
        configured_homes = args.codex_home or settings.codex_home_list
        codex_homes = [path.resolve(strict=True) for path in configured_homes]
        if args.import_existing and not args.watch:
            results = [
                sync_one(
                    path,
                    settings,
                    index=args.index,
                    generation_provider=generation_provider,
                )
                for path in _transcripts(codex_homes)
            ]
            print(
                json.dumps(
                    {
                        "sessions_seen": len(results),
                        "pages_changed": sum(item.changed for item in results),
                        "pages_indexed": sum(item.indexed for item in results),
                    }
                )
            )
            return 0
        if args.watch:
            watch(
                settings,
                codex_homes,
                args.poll_seconds or settings.codex_capture_poll_seconds,
                index=args.index,
                generation_provider=generation_provider,
                import_existing=args.import_existing,
            )
            return 0
        path = _hook_path() if args.hook_stdin else args.transcript
        if path is None:
            parser.error("--transcript, --hook-stdin, or --watch is required")
        result = sync_one(
            path,
            settings,
            index=args.index,
            generation_provider=generation_provider,
        )
        if args.hook_stdin:
            return 0
        print(
            json.dumps(
                {
                    "session_id": result.session_id,
                    "output_path": str(result.output_path),
                    "message_count": result.message_count,
                    "changed": result.changed,
                    "indexed": result.indexed,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        _log(settings, "capture_failed", error=type(exc).__name__, detail=str(exc))
        if args.hook_stdin:
            return 0
        raise


if __name__ == "__main__":
    raise SystemExit(main())
