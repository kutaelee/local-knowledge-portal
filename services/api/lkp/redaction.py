"""Deterministic display redaction for locally collected activity data.

Activity and journal records are useful provenance, but they are not a secret
store. This module deliberately redacts values both while collecting new text
and while serializing historical rows so a pre-policy record cannot re-expose a
credential through the portal.
"""

from __future__ import annotations

import re
from typing import Any

MAX_REDACTED_TEXT_CHARS = 16_384

SECRET_FIELD = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)"
)
PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
SECRET_ASSIGNMENT = re.compile(
    r"(?ix)\b("
    r"api[_ -]?key|access[_ -]?token|pairing[_ -]?token|token|secret|"
    r"password|passwd|authorization|cookie"
    r")(\s*[:=]\s*)([\"'`]?)"
    r"([^\s\"'`,;]{8,})([\"'`]?)"
)


def redact_text(value: str, *, max_chars: int | None = None) -> str:
    """Mask secret-shaped values without claiming to classify all private text."""

    redacted = PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    redacted = BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = OPENAI_KEY.sub("[REDACTED API KEY]", redacted)
    redacted = SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    limit = MAX_REDACTED_TEXT_CHARS if max_chars is None else max_chars
    if len(redacted) > limit:
        omitted = len(redacted) - limit
        return redacted[:limit] + f"\n[TRUNCATED {omitted} chars]"
    return redacted


def redact_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return an API-safe, bounded copy of nested activity metadata."""

    if depth > 12:
        return "[MAX_DEPTH]"
    if SECRET_FIELD.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            str(item_key)[:200]: redact_value(
                item_value, key=str(item_key), depth=depth + 1
            )
            for item_key, item_value in list(value.items())[:200]
        }
    if isinstance(value, list):
        return [redact_value(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact_text(str(value))
