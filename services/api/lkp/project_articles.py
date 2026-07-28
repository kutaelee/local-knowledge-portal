from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .models import (
    Document,
    DocumentState,
    DocumentVersion,
    ProjectArticle,
    ProjectArticleRevision,
    ProjectJournalEntry,
)
from .settings import Settings

PROJECT_ARTICLE_DOCUMENT_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}


@dataclass(frozen=True, slots=True)
class ProjectArticlePlan:
    project: str
    status: str
    revision_number: int | None
    last_compared_at: datetime | None
    source_latest_at: datetime | None
    due_reason: str | None


def _latest_source_times(session: Session) -> dict[str, datetime]:
    latest: dict[str, datetime] = {}
    document_rows = session.execute(
        select(
            Document.project_key,
            func.max(DocumentVersion.detected_at),
        )
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(
            Document.project_key.is_not(None),
            Document.project_key != "",
            Document.state == DocumentState.active,
            Document.extension.in_(PROJECT_ARTICLE_DOCUMENT_EXTENSIONS),
            ~Document.relative_path.contains("/_generated/"),
            ~Document.relative_path.startswith("_generated/"),
        )
        .group_by(Document.project_key)
    )
    for project, detected_at in document_rows:
        if project and detected_at is not None:
            latest[str(project)] = detected_at
    journal_rows = session.execute(
        select(
            ProjectJournalEntry.project_key,
            func.max(ProjectJournalEntry.updated_at),
        )
        .where(ProjectJournalEntry.verification_status != "OUT_OF_PROJECT_SCOPE")
        .group_by(ProjectJournalEntry.project_key)
    )
    for project, updated_at in journal_rows:
        if not project or updated_at is None:
            continue
        current = latest.get(str(project))
        if current is None or updated_at > current:
            latest[str(project)] = updated_at
    repository_rows = session.execute(
        text(
            """
            SELECT p.canonical_name, p.display_name, max(s.created_at) AS analyzed_at
            FROM repository_project p
            JOIN repository_snapshot s ON s.project_id = p.id
            GROUP BY p.id, p.canonical_name, p.display_name
            """
        )
    )
    for canonical_name, display_name, analyzed_at in repository_rows:
        if analyzed_at is None:
            continue
        project = str(canonical_name or display_name or "")
        if not project:
            continue
        current = latest.get(project)
        if current is None or analyzed_at > current:
            latest[project] = analyzed_at
    return latest


def _configuration_matches(
    revision: ProjectArticleRevision,
    settings: Settings,
) -> bool:
    configured_digest = settings.generation_model_digest
    digest_matches = (
        configured_digest in {"", "unresolved"}
        or revision.model_digest == configured_digest
    )
    return (
        revision.prompt_version == settings.project_article_prompt_version
        and revision.model == settings.generation_model
        and digest_matches
    )


def _due_reason(
    article: ProjectArticle | None,
    revision: ProjectArticleRevision | None,
    *,
    source_latest_at: datetime | None,
    settings: Settings,
) -> str | None:
    if article is None:
        return "missing"
    if revision is None:
        return "retry" if article.status == "error" else "pending"
    if article.status in {"error", "pending", "processing", "stale", "degraded"}:
        if article.status == "degraded":
            return "retry"
        return "retry" if article.status == "error" else article.status
    if not _configuration_matches(revision, settings):
        return "editor_revision_changed"
    if article.last_compared_at is None:
        return "never_compared"
    if source_latest_at is not None and source_latest_at > article.last_compared_at:
        return "source_changed"
    return None


def project_article_plan(
    session: Session,
    settings: Settings,
) -> list[ProjectArticlePlan]:
    """Return every project in fair refresh order.

    Missing projects always enter the plan first. Failed and interrupted work
    rotates by its last attempt time, followed by changed sources. This avoids
    alphabetical starvation when the configured per-run budget is smaller than
    the number of registered repositories.
    """

    latest_by_project = _latest_source_times(session)
    articles = {
        row.project_key: row for row in session.scalars(select(ProjectArticle))
    }
    revision_ids = [
        article.current_revision_id
        for article in articles.values()
        if article.current_revision_id is not None
    ]
    revisions = {
        row.id: row
        for row in session.scalars(
            select(ProjectArticleRevision).where(
                ProjectArticleRevision.id.in_(revision_ids)
            )
        )
    }
    never = datetime.min.replace(tzinfo=timezone.utc)
    priority = {
        "missing": 0,
        "pending": 1,
        "retry": 2,
        "processing": 2,
        "stale": 3,
        "editor_revision_changed": 4,
        "never_compared": 5,
        "source_changed": 6,
    }
    result: list[ProjectArticlePlan] = []
    for project, source_latest_at in latest_by_project.items():
        article = articles.get(project)
        revision = (
            revisions.get(article.current_revision_id)
            if article is not None and article.current_revision_id is not None
            else None
        )
        reason = _due_reason(
            article,
            revision,
            source_latest_at=source_latest_at,
            settings=settings,
        )
        result.append(
            ProjectArticlePlan(
                project=project,
                status=article.status if article is not None else "pending",
                revision_number=revision.revision_number if revision is not None else None,
                last_compared_at=(
                    article.last_compared_at if article is not None else None
                ),
                source_latest_at=source_latest_at,
                due_reason=reason,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            priority.get(item.due_reason or "", 99),
            item.last_compared_at or never,
            item.project.casefold(),
        ),
    )


def project_article_summary(
    session: Session,
    settings: Settings,
) -> dict[str, int]:
    plan = project_article_plan(session, settings)
    return {
        "projects": len(plan),
        "current": sum(item.due_reason is None for item in plan),
        "due": sum(item.due_reason is not None for item in plan),
        "missing": sum(item.due_reason == "missing" for item in plan),
        "failed": sum(item.status == "error" for item in plan),
        "processing": sum(item.status == "processing" for item in plan),
        "per_run_limit": settings.project_article_max_projects_per_run,
    }
