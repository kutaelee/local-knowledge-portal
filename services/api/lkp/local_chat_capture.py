from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from lkp_indexer.hook_spool import atomic_spool

from .redaction import redact_value
from .schemas import LocalChatCapture


def spool_local_chat(request: LocalChatCapture, root: Path) -> tuple[str, Path]:
    """Atomically spool one completed local-model turn.

    This is deliberately separate from the global Codex hook spool. The
    collector later owns database ingestion and idempotency.
    """

    payload = redact_value(
        {
            "timestamp": (request.occurred_at or datetime.now(timezone.utc)).isoformat(),
            "source": "local_llm_chat",
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "project_key": request.project_key,
            "model": request.model,
            "user_message": request.user_message,
            "assistant_message": request.assistant_message,
            "metadata": request.metadata,
        }
    )
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_hash = hashlib.sha256(canonical).hexdigest()
    event_id = hashlib.sha256(
        (
            f"local_llm_chat:{request.session_id}:{request.turn_id}:"
            f"{request.model}:{payload_hash}"
        ).encode()
    ).hexdigest()
    envelope = {
        "schema_version": 1,
        "event_id": event_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "status": "accepted",
        "payload_hash": payload_hash,
        "payload_bytes": len(canonical),
        "event_name": "LocalChatCompleted",
        "session_id": request.session_id,
        "turn_id": request.turn_id,
        "cwd": None,
        "tool_name": None,
        "payload": payload,
    }
    return event_id, atomic_spool(root, envelope, "pending")
