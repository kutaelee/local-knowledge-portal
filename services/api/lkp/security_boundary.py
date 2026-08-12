"""Non-bypassable project security boundary shared by API and indexer code."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, literal_column, or_
from sqlalchemy.orm import Session, with_loader_criteria


class ProhibitedProjectError(RuntimeError):
    """Raised without echoing a prohibited identifier or payload."""


# This baseline is deliberately code-owned: environment configuration may add
# restrictions later, but it must never remove the mandatory boundary.
_PROHIBITED_PROJECT_KEYS = frozenset({"esb"})
_GENERIC_MESSAGE = "project is outside the permitted local knowledge scope"
_installed = False


def normalize_project_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def is_prohibited_project(value: object) -> bool:
    return normalize_project_key(value) in _PROHIBITED_PROJECT_KEYS


def text_references_prohibited_project(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return any(
        re.search(rf"(?<![a-z0-9_]){re.escape(key)}(?![a-z0-9_])", normalized)
        for key in _PROHIBITED_PROJECT_KEYS
    )


def require_allowed_project(value: object) -> None:
    if is_prohibited_project(value):
        raise ProhibitedProjectError(_GENERIC_MESSAGE)


def path_references_prohibited_project(path: Path, source_root: Path | None = None) -> bool:
    """Check path components only; never open or inspect the target."""

    raw_parts = [part for part in str(path).replace("\\", "/").split("/") if part]
    if any(is_prohibited_project(part) for part in raw_parts):
        return True
    candidate = path.resolve(strict=False)
    if source_root is not None:
        root = source_root.resolve(strict=False)
        try:
            parts = candidate.relative_to(root).parts
        except ValueError:
            return True
    else:
        parts = candidate.parts
    return any(is_prohibited_project(part) for part in parts)


def require_allowed_path(path: Path, source_root: Path | None = None) -> None:
    if path_references_prohibited_project(path, source_root):
        raise ProhibitedProjectError(_GENERIC_MESSAGE)


def allowed_project_expression(column):
    normalized = func.lower(func.btrim(func.normalize(column, literal_column("NFKC"))))
    return or_(column.is_(None), normalized.not_in(tuple(_PROHIBITED_PROJECT_KEYS)))


def allowed_metadata_project_expression(column):
    project = column["project"].astext
    normalized = func.lower(func.btrim(func.normalize(project, literal_column("NFKC"))))
    return or_(project.is_(None), normalized.not_in(tuple(_PROHIBITED_PROJECT_KEYS)))


def allowed_path_expression(column):
    normalized = func.lower(
        func.replace(
            func.normalize(column, literal_column("NFKC")),
            "\\",
            "/",
        )
    )
    checks = [
        ~normalized.op("~")(rf"(^|/)[[:space:]]*{re.escape(key)}[[:space:]]*($|/)")
        for key in _PROHIBITED_PROJECT_KEYS
    ]
    expression = checks[0]
    for check in checks[1:]:
        expression &= check
    return or_(column.is_(None), expression)


def repository_project_sql(alias: str = "p") -> str:
    if not alias.replace("_", "").isalnum():
        raise ValueError("unsafe SQL alias")
    denied = ", ".join(f"'{key}'" for key in sorted(_PROHIBITED_PROJECT_KEYS))
    return (
        f"lower(btrim(normalize(coalesce({alias}.canonical_name, ''), NFKC))) "
        f"NOT IN ({denied}) AND "
        f"lower(btrim(normalize(coalesce({alias}.display_name, ''), NFKC))) "
        f"NOT IN ({denied})"
    )


def project_column_sql(column: str) -> str:
    if not column.replace("_", "").replace(".", "").isalnum():
        raise ValueError("unsafe SQL column")
    denied = ", ".join(f"'{key}'" for key in sorted(_PROHIBITED_PROJECT_KEYS))
    return f"lower(btrim(normalize(coalesce({column}, ''), NFKC))) NOT IN ({denied})"


def metadata_project_sql(column: str = "metadata") -> str:
    if not column.replace("_", "").replace(".", "").isalnum():
        raise ValueError("unsafe SQL column")
    denied = ", ".join(f"'{key}'" for key in sorted(_PROHIBITED_PROJECT_KEYS))
    return f"lower(btrim(normalize(coalesce({column}->>'project', ''), NFKC))) NOT IN ({denied})"


def path_column_sql(column: str) -> str:
    if not column.replace("_", "").replace(".", "").isalnum():
        raise ValueError("unsafe SQL column")
    normalized = f"lower(replace(normalize(coalesce({column}, ''), NFKC), '\\\\', '/'))"
    checks = [
        f"{normalized} !~ '(^|/)[[:space:]]*{re.escape(key)}[[:space:]]*($|/)'"
        for key in sorted(_PROHIBITED_PROJECT_KEYS)
    ]
    return " AND ".join(checks)


def project_is_allowed_record(value: dict[str, Any]) -> bool:
    return not any(
        is_prohibited_project(value.get(field))
        for field in ("project", "project_key", "canonical_name", "display_name")
    )


def _object_project_is_prohibited(instance: object) -> bool:
    direct = getattr(instance, "project_key", None)
    if is_prohibited_project(direct):
        return True
    metadata = getattr(instance, "metadata_json", None)
    return isinstance(metadata, dict) and is_prohibited_project(metadata.get("project"))


def install_session_security_guards() -> None:
    """Install query and persistence guards once for all application Sessions."""

    global _installed
    if _installed:
        return

    from .models import (
        ActivityEvent,
        DeveloperFeedPost,
        Document,
        IngestEvent,
        IngestJob,
        KnowledgeCandidate,
        KnowledgeCase,
        ProjectArticle,
        ProjectJournalEntry,
    )

    project_models = (
        Document,
        ActivityEvent,
        ProjectJournalEntry,
        DeveloperFeedPost,
        ProjectArticle,
    )
    metadata_models = (KnowledgeCandidate, KnowledgeCase)
    path_models = (IngestJob, IngestEvent)

    @event.listens_for(Session, "do_orm_execute")
    def _exclude_prohibited_rows(execute_state) -> None:
        if not execute_state.is_select:
            return
        statement = execute_state.statement
        for model in project_models:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    allowed_project_expression(model.project_key),
                    include_aliases=True,
                )
            )
        for model in metadata_models:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    allowed_metadata_project_expression(model.metadata_json),
                    include_aliases=True,
                )
            )
        for model in path_models:
            statement = statement.options(
                with_loader_criteria(
                    model,
                    allowed_path_expression(
                        model.canonical_path if model is IngestJob else model.path
                    ),
                    include_aliases=True,
                )
            )
        execute_state.statement = statement

    @event.listens_for(Session, "before_flush")
    def _reject_prohibited_writes(session: Session, _flush_context, _instances) -> None:
        for instance in session.new.union(session.dirty):
            if _object_project_is_prohibited(instance):
                raise ProhibitedProjectError(_GENERIC_MESSAGE)
            if isinstance(instance, IngestJob):
                require_allowed_path(Path(instance.canonical_path))
            elif isinstance(instance, IngestEvent) and instance.path:
                require_allowed_path(Path(instance.path))

    _installed = True


def generic_policy_denial() -> dict[str, Any]:
    return {
        "confidence": "none",
        "no_answer": True,
        "navigation_available": False,
        "contexts": [],
        "navigation": [],
        "policy": "project_scope_denied",
    }
