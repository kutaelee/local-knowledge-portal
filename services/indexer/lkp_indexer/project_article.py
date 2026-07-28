from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from lkp.db import SessionLocal
from lkp.models import (
    Document,
    DocumentChunk,
    DocumentState,
    GeneratedPage,
    ProjectArticle,
    ProjectArticleRevision,
    ProjectJournalEntry,
    SourceRoot,
)
from lkp.project_articles import (
    PROJECT_ARTICLE_DOCUMENT_EXTENSIONS,
    project_article_plan,
)
from lkp.redaction import redact_text
from lkp.settings import Settings, get_settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .generation import (
    GenerationProvider,
    ProjectArticleDraft,
    build_generation_provider,
)
from .paths import idempotency_key
from .queue import enqueue

logger = structlog.get_logger()

_DOCUMENT_EXTENSIONS = PROJECT_ARTICLE_DOCUMENT_EXTENSIONS
_PROMPT_VERSION = "project-article-v3-hierarchical-source-manifest"


@dataclass(frozen=True, slots=True)
class ArticleSource:
    id: str
    source_type: str
    content_hash: str
    title: str
    body: str
    provenance: dict[str, Any]

    def catalog(self, *, include_excerpt: bool = False) -> dict[str, Any]:
        value = {
            "id": self.id,
            "source_type": self.source_type,
            "content_hash": self.content_hash,
            "title": self.title,
            **self.provenance,
        }
        if include_excerpt:
            value["excerpt"] = self.body[:4000]
        return value


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _journal_sources(session: Session, project: str) -> list[ArticleSource]:
    rows = list(
        session.scalars(
            select(ProjectJournalEntry)
            .where(
                ProjectJournalEntry.project_key == project,
                ProjectJournalEntry.verification_status != "OUT_OF_PROJECT_SCOPE",
            )
            .order_by(ProjectJournalEntry.occurred_at, ProjectJournalEntry.id)
        )
    )
    sources: list[ArticleSource] = []
    for row in rows:
        payload = {
            "intent": row.intent,
            "reported_change": row.change_summary,
            "failures": row.failures_json,
            "resolution": row.resolution,
            "verification": row.verification_json,
            "changed_files": row.changed_files,
            "verification_status": row.verification_status,
        }
        body = redact_text(json.dumps(payload, ensure_ascii=False, indent=2))
        sources.append(
            ArticleSource(
                id=f"J:{row.id}",
                source_type="project_journal",
                content_hash=_hash_json(payload),
                title=redact_text(row.title),
                body=body,
                provenance={
                    "journal_entry_id": str(row.id),
                    "occurred_at": row.occurred_at.isoformat(),
                    "verification_status": row.verification_status,
                },
            )
        )
    return sources


def _document_sources(session: Session, project: str) -> list[ArticleSource]:
    rows = session.execute(
        select(Document, DocumentChunk)
        .join(
            DocumentChunk,
            DocumentChunk.document_version_id == Document.current_version_id,
        )
        .where(
            Document.project_key == project,
            Document.state == DocumentState.active,
            Document.extension.in_(_DOCUMENT_EXTENSIONS),
            ~Document.relative_path.contains("/_generated/"),
            ~Document.relative_path.startswith("_generated/"),
        )
        .order_by(Document.relative_path, DocumentChunk.chunk_index)
    ).all()
    sources: list[ArticleSource] = []
    for document, chunk in rows:
        body = redact_text(chunk.content)
        sources.append(
            ArticleSource(
                id=f"D:{document.id}:{chunk.chunk_index}",
                source_type="document_chunk",
                content_hash=chunk.content_hash,
                title=(
                    f"{document.relative_path}"
                    + (f" — {chunk.heading_path}" if chunk.heading_path else "")
                ),
                body=body,
                provenance={
                    "document_id": str(document.id),
                    "document_version_id": str(document.current_version_id),
                    "chunk_id": str(chunk.id),
                    "canonical_path": document.canonical_path,
                    "relative_path": document.relative_path,
                    "modified_at": document.modified_at_fs.isoformat(),
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                },
            )
        )
    return sources


def _repository_sources(session: Session, project: str) -> list[ArticleSource]:
    """Expose only source-verified repository facts to the canonical article."""
    row = session.execute(
        text(
            """
            SELECT p.id AS project_id, p.canonical_name, p.display_name,
                   s.id AS snapshot_id, s.snapshot_name, s.source_hash,
                   s.git_commit, s.git_branch, s.dirty_worktree, s.file_count,
                   s.languages, s.build_systems, s.status, s.stale, s.created_at
            FROM repository_project p
            JOIN LATERAL (
              SELECT *
              FROM repository_snapshot candidate
              WHERE candidate.project_id = p.id
              ORDER BY candidate.created_at DESC
              LIMIT 1
            ) s ON true
            WHERE lower(p.canonical_name) = lower(:project)
               OR lower(p.display_name) = lower(:project)
            ORDER BY
              CASE WHEN lower(p.canonical_name) = lower(:project) THEN 0 ELSE 1 END
            LIMIT 1
            """
        ),
        {"project": project},
    ).mappings().one_or_none()
    if row is None:
        return []
    facts = [
        dict(item)
        for item in session.execute(
            text(
                """
                SELECT knowledge_type, title, summary, detail, source_references,
                       validation_status, confidence
                FROM repository_knowledge_item
                WHERE snapshot_id = :snapshot_id
                  AND searchable = true
                  AND validation_status = 'SOURCE_VERIFIED'
                  AND jsonb_array_length(source_references) > 0
                ORDER BY knowledge_type, title
                LIMIT 40
                """
            ),
            {"snapshot_id": row["snapshot_id"]},
        ).mappings()
    ]
    payload = {
        "snapshot": {
            key: (
                value.isoformat()
                if isinstance(value, datetime)
                else str(value)
                if key in {"snapshot_id", "project_id"}
                else value
            )
            for key, value in dict(row).items()
        },
        "source_verified_facts": facts,
    }
    return [
        ArticleSource(
            id=f"R:{row['snapshot_id']}",
            source_type="repository_snapshot",
            content_hash=str(row["source_hash"]),
            title=f"{row['display_name']} repository analysis",
            body=redact_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str)),
            provenance={
                "repository_project_id": str(row["project_id"]),
                "repository_snapshot_id": str(row["snapshot_id"]),
                "analyzed_at": row["created_at"].isoformat(),
                "stale": bool(row["stale"]),
                "source_verified_fact_count": len(facts),
            },
        )
    ]


def collect_project_sources(session: Session, project: str) -> list[ArticleSource]:
    sources = [
        *_document_sources(session, project),
        *_journal_sources(session, project),
        *_repository_sources(session, project),
    ]
    return sorted(sources, key=lambda item: (item.source_type, item.title, item.id))


def _source_hash(sources: list[ArticleSource]) -> str:
    return _hash_json([(item.id, item.content_hash) for item in sources])


def _used_source_ids(draft: ProjectArticleDraft | None) -> list[str]:
    if draft is None:
        return []
    result: list[str] = []
    for paragraph in [
        draft.standfirst,
        *[
            paragraph
            for section in draft.sections
            for paragraph in section.paragraphs
        ],
    ]:
        for sentence in paragraph.sentences:
            for source_id in sentence.source_ids:
                if source_id not in result:
                    result.append(source_id)
    return result


def _validate_draft(
    draft: ProjectArticleDraft,
    allowed_ids: set[str],
    *,
    required_source_groups: list[set[str]] | None = None,
) -> None:
    cited = _used_source_ids(draft)
    invalid = sorted(set(cited) - allowed_ids)
    if invalid:
        raise ValueError(f"project article cited unknown or removed sources: {invalid[:10]}")
    if not cited:
        raise ValueError("project article did not cite any source")
    sentences = [
        sentence
        for paragraph in [
            draft.standfirst,
            *[
                paragraph
                for section in draft.sections
                for paragraph in section.paragraphs
            ],
        ]
        for sentence in paragraph.sentences
    ]
    if any("\n" in sentence.text or "\r" in sentence.text for sentence in sentences):
        raise ValueError("project article sentence contains block Markdown")
    missing_groups = [
        index
        for index, group in enumerate(required_source_groups or [], start=1)
        if group and not (set(cited) & group)
    ]
    if missing_groups:
        raise ValueError(
            "project article omitted an evidence batch: "
            f"{missing_groups[:10]}"
        )


def _covered_group_indexes(
    draft: ProjectArticleDraft,
    source_groups: list[set[str]],
) -> set[int]:
    cited = set(_used_source_ids(draft))
    return {
        index
        for index, group in enumerate(source_groups)
        if group and cited.intersection(group)
    }


def _batch_sources(
    sources: list[ArticleSource],
    *,
    max_chars: int,
) -> list[list[ArticleSource]]:
    batches: list[list[ArticleSource]] = []
    current: list[ArticleSource] = []
    current_chars = 0
    for source in sources:
        size = len(source.body) + len(source.title) + 200
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(source)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _batch_drafts(
    drafts: list[ProjectArticleDraft],
    *,
    max_chars: int,
) -> list[list[ProjectArticleDraft]]:
    batches: list[list[ProjectArticleDraft]] = []
    current: list[ProjectArticleDraft] = []
    current_chars = 0
    for draft in drafts:
        size = len(
            json.dumps(
                draft.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(draft)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _note_cache_key(
    *,
    project: str,
    phase: str,
    inputs: Any,
    provider: GenerationProvider,
    settings: Settings,
) -> str:
    return _hash_json(
        {
            "project": project,
            "phase": phase,
            "inputs": inputs,
            "provider": provider.provider,
            "model": provider.model,
            "configured_digest": settings.generation_model_digest,
            "prompt_version": settings.project_article_prompt_version,
        }
    )


def _note_cache_path(
    settings: Settings,
    *,
    project: str,
    key: str,
) -> Path:
    project_key = hashlib.sha256(project.encode("utf-8")).hexdigest()[:16]
    return (
        settings.runtime_dir
        / "cache"
        / "project-article-notes"
        / project_key
        / f"{key}.json"
    )


def _load_cached_note(
    settings: Settings,
    *,
    project: str,
    key: str,
    allowed_ids: set[str],
) -> ProjectArticleDraft | None:
    path = _note_cache_path(settings, project=project, key=key)
    try:
        draft = ProjectArticleDraft.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        _validate_draft(draft, allowed_ids)
        return draft
    except (OSError, ValueError):
        return None


def _store_cached_note(
    settings: Settings,
    *,
    project: str,
    key: str,
    draft: ProjectArticleDraft,
) -> None:
    target = _note_cache_path(settings, project=project, key=key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".note.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(draft.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _draft_from_revision(
    revision: ProjectArticleRevision | None,
) -> ProjectArticleDraft | None:
    if revision is None:
        return None
    return ProjectArticleDraft.model_validate(
        {
            "title": revision.title,
            "standfirst": revision.standfirst_json,
            "sections": revision.sections_json,
        }
    )


def _revision_sources(
    revision: ProjectArticleRevision | None,
) -> dict[str, dict[str, Any]]:
    if revision is None:
        return {}
    manifest = revision.source_manifest_json or revision.sources_json
    return {
        str(item["id"]): item
        for item in manifest
        if isinstance(item, dict) and item.get("id")
    }


def _source_payload(source: ArticleSource) -> dict[str, Any]:
    return {**source.catalog(), "body": source.body}


def _remap_draft_source_ids(
    draft: ProjectArticleDraft | None,
    mapping: dict[str, str],
) -> ProjectArticleDraft | None:
    if draft is None:
        return None
    value = draft.model_dump(mode="json")
    paragraphs = [
        value["standfirst"],
        *[
            paragraph
            for section in value["sections"]
            for paragraph in section["paragraphs"]
        ],
    ]
    for paragraph in paragraphs:
        for sentence in paragraph["sentences"]:
            sentence["source_ids"] = [
                mapping[source_id]
                for source_id in sentence["source_ids"]
                if source_id in mapping
            ]
    return ProjectArticleDraft.model_validate(value)


def _with_source_alias(value: dict[str, Any], alias: str) -> dict[str, Any]:
    return {**value, "id": alias}


def _prompt_catalog_entry(value: dict[str, Any], alias: str) -> dict[str, Any]:
    return _with_source_alias(
        {key: item for key, item in value.items() if key != "excerpt"},
        alias,
    )


def _editorial_configuration_matches(
    revision: ProjectArticleRevision,
    *,
    provider: GenerationProvider,
    settings: Settings,
) -> bool:
    configured_digest = settings.generation_model_digest
    digest_matches = (
        settings.generation_model != provider.model
        or configured_digest in {"", "unresolved"}
        or revision.model_digest == configured_digest
    )
    return (
        revision.prompt_version == settings.project_article_prompt_version
        and revision.provider == provider.provider
        and revision.model == provider.model
        and digest_matches
    )


def _render_markdown(
    project: str,
    draft: ProjectArticleDraft,
    sources: list[dict[str, Any]],
    *,
    revision_number: int,
    generated_at: datetime,
    pipeline_version: str,
) -> str:
    source_by_id = {str(item["id"]): item for item in sources}

    def sentence_text(sentence: Any) -> str:
        citations = " ".join(
            f"[{source_by_id[item]['citation_number']}]"
            for item in sentence.source_ids
            if item in source_by_id
        )
        return f"{sentence.text.strip()} {citations}".strip()

    lines = [
        "---",
        "managed: true",
        "generator: local-knowledge-portal",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        f"pipeline_version: {json.dumps(pipeline_version)}",
        f"generated_at: {json.dumps(generated_at.isoformat())}",
        f"revision: {revision_number}",
        'page_type: "project-canonical-article"',
        "---",
        "",
        f"# {draft.title}",
        "",
        "> "
        + " ".join(sentence_text(item) for item in draft.standfirst.sentences),
        "",
    ]
    for section in draft.sections:
        lines.extend([f"## {section.title}", ""])
        for paragraph in section.paragraphs:
            lines.extend(
                [
                    " ".join(sentence_text(item) for item in paragraph.sentences),
                    "",
                ]
            )
    lines.extend(["## 근거", ""])
    for source in sources:
        location = source.get("relative_path") or source.get("title") or source["id"]
        line_range = (
            f":{source['start_line']}-{source['end_line']}"
            if source.get("start_line") is not None
            else ""
        )
        lines.append(
            f"- [{source['citation_number']}] `{location}{line_range}` "
            f"({source['source_type']})"
        )
    lines.append("")
    return "\n".join(lines)


def _write_managed_article(
    session: Session,
    *,
    project: str,
    draft: ProjectArticleDraft,
    revision: ProjectArticleRevision,
    settings: Settings,
) -> Path:
    safe_project = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in project
    ).strip("-") or "unknown-project"
    relative_path = (
        Path("_generated") / "Projects" / safe_project / "project-overview.md"
    )
    managed_root = (settings.vault_dir / "_generated" / "Projects").resolve(
        strict=False
    )
    target = (settings.vault_dir / relative_path).resolve(strict=False)
    target.relative_to(managed_root)
    if target.exists():
        header = target.read_text(encoding="utf-8", errors="strict")[:4096]
        if (
            "managed: true" not in header
            or "generator: local-knowledge-portal" not in header
        ):
            raise PermissionError(f"refusing to overwrite non-managed article: {target}")
    content = _render_markdown(
        project,
        draft,
        revision.sources_json,
        revision_number=revision.revision_number,
        generated_at=revision.created_at,
        pipeline_version=settings.pipeline_version,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".project-overview.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    relative_posix = relative_path.as_posix()
    generated = session.scalar(
        select(GeneratedPage).where(GeneratedPage.relative_path == relative_posix)
    )
    if generated is None:
        generated = GeneratedPage(relative_path=relative_posix)
        session.add(generated)
    generated.source_hashes = [revision.source_hash]
    generated.pipeline_version = settings.pipeline_version
    generated.generated_at = revision.created_at
    root = session.scalar(
        select(SourceRoot).where(
            SourceRoot.canonical_path
            == str(settings.vault_dir.resolve(strict=False))
        )
    )
    if root is not None:
        info = target.stat()
        enqueue(
            session,
            key=idempotency_key(
                str(root.id), str(target), info.st_size, info.st_mtime_ns
            ),
            source_root_id=root.id,
            canonical_path=str(target),
            job_type="index",
            priority=20,
            details={
                "project": project,
                "page_type": "project_canonical_article",
                "article_revision_id": str(revision.id),
                "source_hash": revision.source_hash,
            },
            coalesce_pending=True,
        )
    return target


def refresh_project_article(
    session: Session,
    *,
    project: str,
    provider: GenerationProvider,
    settings: Settings,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    sources = collect_project_sources(session, project)
    if not sources:
        return {"project": project, "status": "no_sources"}
    new_hash = _source_hash(sources)
    article = session.scalar(
        select(ProjectArticle).where(ProjectArticle.project_key == project)
    )
    if article is None:
        article = ProjectArticle(project_key=project)
        session.add(article)
        session.flush()
    current_revision = (
        session.get(ProjectArticleRevision, article.current_revision_id)
        if article.current_revision_id
        else None
    )
    article.last_compared_at = now
    configuration_matches = (
        current_revision is not None
        and _editorial_configuration_matches(
            current_revision,
            provider=provider,
            settings=settings,
        )
    )
    if (
        current_revision is not None
        and current_revision.source_hash == new_hash
        and configuration_matches
    ):
        article.status = "current"
        session.flush()
        return {"project": project, "status": "unchanged"}

    # A prompt/model revision is a full editorial rebuild. Reusing prose made
    # by an obsolete editor can anchor the new model to stale or low-quality
    # structure even when every source is read again.
    rebuild = current_revision is not None and not configuration_matches
    previous_sources = {} if rebuild else _revision_sources(current_revision)
    source_by_id = {item.id: item for item in sources}
    current_catalog = {
        item.id: item.catalog(include_excerpt=True) for item in sources
    }
    changed = [
        item
        for item in sources
        if item.id not in previous_sources
        or previous_sources[item.id].get("content_hash") != item.content_hash
    ]
    # A major source expansion is also a full rebuild. Otherwise an old short
    # article anchors the editor while a newly registered repository contributes
    # most of the current documentation.
    if (
        current_revision is not None
        and not rebuild
        and len(changed) >= max(20, len(previous_sources) // 2)
    ):
        rebuild = True
        previous_sources = {}
        changed = list(sources)
    removed = sorted(set(previous_sources) - set(source_by_id))
    # Small local models occasionally mutate long UUID-backed citation IDs.
    # Give the model short deterministic aliases and translate them back before
    # validation/storage; the fail-closed provenance gate still checks the
    # restored IDs against the complete current catalog.
    all_source_ids = sorted(set(current_catalog) | set(previous_sources))
    alias_by_id = {
        source_id: f"S{index:04d}"
        for index, source_id in enumerate(all_source_ids, start=1)
    }
    id_by_alias = {alias: source_id for source_id, alias in alias_by_id.items()}
    batches = _batch_sources(
        changed,
        max_chars=settings.project_article_batch_chars,
    )
    previous_draft = None if rebuild else _draft_from_revision(current_revision)
    editorial_notes: list[ProjectArticleDraft] = []
    model_digest = settings.generation_model_digest
    note_cache_hits = 0
    for batch_index, batch in enumerate(batches, start=1):
        batch_ids = {item.id for item in batch}
        cache_key = _note_cache_key(
            project=project,
            phase="evidence_digest",
            inputs=[(item.id, item.content_hash) for item in batch],
            provider=provider,
            settings=settings,
        )
        note = _load_cached_note(
            settings,
            project=project,
            key=cache_key,
            allowed_ids=batch_ids,
        )
        if note is not None:
            editorial_notes.append(note)
            note_cache_hits += 1
            continue
        catalog = [
            _prompt_catalog_entry(
                current_catalog[source_id],
                alias_by_id[source_id],
            )
            for source_id in sorted(batch_ids)
        ]
        payload = {
            "project": project,
            "phase": "evidence_digest",
            "update_mode": "initial" if current_revision is None else "incremental",
            "batch": {"index": batch_index, "count": len(batches)},
            "previous_article": None,
            "source_catalog": catalog,
            "changed_sources": [
                _with_source_alias(
                    _source_payload(item),
                    alias_by_id[item.id],
                )
                for item in batch
            ],
            "removed_source_ids": (
                [alias_by_id[source_id] for source_id in removed]
                if batch_index == 1
                else []
            ),
        }
        aliased_note, model_digest = provider.curate_project_article(
            payload,
            language=settings.knowledge_content_language,
            prompt_version=settings.project_article_prompt_version,
        )
        note = _remap_draft_source_ids(aliased_note, id_by_alias)
        if note is None:  # pragma: no cover - provider contract excludes this
            raise ValueError("project article provider returned no evidence digest")
        _validate_draft(note, batch_ids)
        _store_cached_note(
            settings,
            project=project,
            key=cache_key,
            draft=note,
        )
        editorial_notes.append(note)

    initial_note_count = len(editorial_notes)
    consolidation_rounds = 0
    while len(editorial_notes) > 1:
        note_batches = _batch_drafts(
            editorial_notes,
            max_chars=max(4000, settings.project_article_batch_chars // 4),
        )
        if len(note_batches) >= len(editorial_notes):
            break
        consolidated: list[ProjectArticleDraft] = []
        previous_note_count = len(editorial_notes)
        consolidation_rounds += 1
        for note_batch_index, note_batch in enumerate(note_batches, start=1):
            group_ids = [
                set(_used_source_ids(note)) for note in note_batch
            ]
            source_ids = set().union(*group_ids)
            cache_key = _note_cache_key(
                project=project,
                phase="consolidation",
                inputs=[
                    note.model_dump(mode="json") for note in note_batch
                ],
                provider=provider,
                settings=settings,
            )
            cached_note = _load_cached_note(
                settings,
                project=project,
                key=cache_key,
                allowed_ids=source_ids,
            )
            catalog = [
                _prompt_catalog_entry(
                    current_catalog[source_id],
                    alias_by_id[source_id],
                )
                for source_id in sorted(source_ids)
                if source_id in current_catalog
            ]
            prompt_notes = [
                remapped.model_dump(mode="json")
                for note in note_batch
                if (
                    remapped := _remap_draft_source_ids(
                        note,
                        alias_by_id,
                    )
                )
                is not None
            ]
            payload = {
                "project": project,
                "phase": "consolidation",
                "update_mode": (
                    "initial" if current_revision is None else "incremental"
                ),
                "batch": {
                    "index": note_batch_index,
                    "count": len(note_batches),
                    "round": consolidation_rounds,
                },
                "previous_article": None,
                "source_catalog": catalog,
                "editorial_notes": prompt_notes,
                "required_source_groups": [
                    [alias_by_id[source_id] for source_id in group]
                    for group in group_ids
                ],
                "changed_sources": [],
                "removed_source_ids": [],
            }
            if cached_note is None:
                aliased_note, model_digest = provider.curate_project_article(
                    payload,
                    language=settings.knowledge_content_language,
                    prompt_version=settings.project_article_prompt_version,
                )
                note = _remap_draft_source_ids(aliased_note, id_by_alias)
                if note is None:  # pragma: no cover - provider contract excludes this
                    raise ValueError(
                        "project article provider returned no consolidated note"
                    )
                _validate_draft(note, set(current_catalog))
                _store_cached_note(
                    settings,
                    project=project,
                    key=cache_key,
                    draft=note,
                )
            else:
                note = cached_note
                note_cache_hits += 1
            cited_ids = set(_used_source_ids(note))
            covered = [
                index
                for index, group in enumerate(group_ids)
                if group & cited_ids
            ]
            if len(covered) < 2:
                # The model did not actually consolidate the group. Preserve
                # every input note rather than assigning omitted evidence to
                # an unrelated sentence or silently losing it.
                consolidated.extend(note_batch)
                continue
            consolidated.append(note)
            consolidated.extend(
                child
                for index, child in enumerate(note_batch)
                if index not in covered
            )
        editorial_notes = consolidated
        if len(editorial_notes) >= previous_note_count:
            break

    synthesis_source_ids = set(_used_source_ids(previous_draft))
    for note in editorial_notes:
        synthesis_source_ids.update(_used_source_ids(note))
    synthesis_source_ids -= set(removed)
    synthesis_catalog = [
        _prompt_catalog_entry(
            current_catalog[source_id],
            alias_by_id[source_id],
        )
        for source_id in sorted(synthesis_source_ids)
        if source_id in current_catalog
    ]
    prompt_previous = _remap_draft_source_ids(previous_draft, alias_by_id)
    prompt_notes = [
        remapped.model_dump(mode="json")
        for note in editorial_notes
        if (remapped := _remap_draft_source_ids(note, alias_by_id)) is not None
    ]
    synthesis_payload = {
        "project": project,
        "phase": "synthesis",
        "update_mode": "initial" if current_revision is None else "incremental",
        "previous_article": (
            prompt_previous.model_dump(mode="json") if prompt_previous else None
        ),
        "source_catalog": synthesis_catalog,
        "editorial_notes": prompt_notes,
        "required_source_groups": [
            [alias_by_id[source_id] for source_id in _used_source_ids(note)]
            for note in editorial_notes
        ],
        "changed_sources": [],
        "removed_source_ids": [alias_by_id[source_id] for source_id in removed],
    }
    aliased_draft, model_digest = provider.curate_project_article(
        synthesis_payload,
        language=settings.knowledge_content_language,
        prompt_version=settings.project_article_prompt_version,
    )
    draft = _remap_draft_source_ids(aliased_draft, id_by_alias)
    if draft is None:  # pragma: no cover - provider contract excludes this
        raise ValueError("project article provider returned no synthesis")
    _validate_draft(draft, set(current_catalog))

    # Editorial notes prove that every source batch was read, but forcing the
    # final article to cite every batch produces a stitched inventory instead
    # of a useful narrative. Require broad top-level coverage, then ask the
    # editor to repair only genuinely missing areas. Keep a repair only when
    # it increases measured evidence-group coverage.
    final_groups = [set(_used_source_ids(note)) for note in editorial_notes]
    coverage_target = (
        max(
            1,
            math.ceil(
                len(final_groups)
                * settings.project_article_min_group_coverage
            ),
        )
        if final_groups
        else 0
    )
    covered_groups = _covered_group_indexes(draft, final_groups)
    coverage_repair_attempts = 0
    for attempt in range(1, 4):
        if len(covered_groups) >= coverage_target:
            break
        missing_indexes = [
            index
            for index in range(len(final_groups))
            if index not in covered_groups
        ]
        missing_notes = [editorial_notes[index] for index in missing_indexes]
        repair_source_ids = set(_used_source_ids(draft))
        for note in missing_notes:
            repair_source_ids.update(_used_source_ids(note))
        repair_catalog = [
            _prompt_catalog_entry(
                current_catalog[source_id],
                alias_by_id[source_id],
            )
            for source_id in sorted(repair_source_ids)
            if source_id in current_catalog
        ]
        prompt_draft = _remap_draft_source_ids(draft, alias_by_id)
        prompt_missing_notes = [
            remapped.model_dump(mode="json")
            for note in missing_notes
            if (remapped := _remap_draft_source_ids(note, alias_by_id)) is not None
        ]
        coverage_payload = {
            "project": project,
            "phase": "coverage_repair",
            "attempt": attempt,
            "update_mode": (
                "initial" if current_revision is None else "incremental"
            ),
            "previous_article": (
                prompt_draft.model_dump(mode="json") if prompt_draft else None
            ),
            "source_catalog": repair_catalog,
            "editorial_notes": prompt_missing_notes,
            "required_source_groups": [
                [
                    alias_by_id[source_id]
                    for source_id in final_groups[index]
                ]
                for index in missing_indexes
            ],
            "changed_sources": [],
            "removed_source_ids": [],
        }
        aliased_repair, model_digest = provider.curate_project_article(
            coverage_payload,
            language=settings.knowledge_content_language,
            prompt_version=settings.project_article_prompt_version,
        )
        repair = _remap_draft_source_ids(aliased_repair, id_by_alias)
        if repair is None:  # pragma: no cover - provider contract excludes this
            break
        _validate_draft(repair, set(current_catalog))
        repaired_coverage = _covered_group_indexes(repair, final_groups)
        coverage_repair_attempts = attempt
        if len(repaired_coverage) <= len(covered_groups):
            break
        draft = repair
        covered_groups = repaired_coverage
    if len(covered_groups) < coverage_target:
        raise ValueError(
            "project article did not meet editorial coverage target: "
            f"{len(covered_groups)}/{coverage_target} groups"
        )

    used_ids = _used_source_ids(draft)
    numbered_sources = []
    for citation_number, source_id in enumerate(used_ids, start=1):
        value = dict(current_catalog[source_id])
        value["citation_number"] = citation_number
        numbered_sources.append(value)
    next_revision = int(
        session.scalar(
            select(func.coalesce(func.max(ProjectArticleRevision.revision_number), 0))
            .where(ProjectArticleRevision.article_id == article.id)
        )
        or 0
    ) + 1
    revision = ProjectArticleRevision(
        article_id=article.id,
        revision_number=next_revision,
        previous_revision_id=current_revision.id if current_revision else None,
        title=draft.title,
        standfirst_json=draft.standfirst.model_dump(mode="json"),
        sections_json=[
            item.model_dump(mode="json") for item in draft.sections
        ],
        sources_json=numbered_sources,
        source_manifest_json=[
            item.catalog(include_excerpt=False) for item in sources
        ],
        source_hash=new_hash,
        provider=provider.provider,
        model=provider.model,
        model_digest=model_digest,
        prompt_version=settings.project_article_prompt_version,
        change_summary_json={
            "new_or_changed_sources": len(changed),
            "removed_sources": len(removed),
            "total_sources": len(sources),
            "cited_sources": len(numbered_sources),
            "batches": len(batches),
            "editorial_note_sources": len(synthesis_source_ids),
            "initial_editorial_notes": initial_note_count,
            "consolidation_rounds": consolidation_rounds,
            "note_cache_hits": note_cache_hits,
            "editorial_groups": len(final_groups),
            "covered_editorial_groups": len(covered_groups),
            "coverage_target": coverage_target,
            "coverage_ratio_setting": (
                settings.project_article_min_group_coverage
            ),
            "coverage_repair_attempts": coverage_repair_attempts,
            "full_rebuild": rebuild,
        },
        created_at=now,
    )
    session.add(revision)
    session.flush()
    article.current_revision_id = revision.id
    article.source_hash = new_hash
    article.status = "current"
    article.updated_at = now
    path = _write_managed_article(
        session,
        project=project,
        draft=draft,
        revision=revision,
        settings=settings,
    )
    session.flush()
    return {
        "project": project,
        "status": "updated",
        "revision": next_revision,
        "sources": len(sources),
        "cited_sources": len(numbered_sources),
        "path": str(path),
    }


_FALLBACK_VERSION = "preserve-editorial-v2"


def _fallback_draft(
    project: str,
    sources: list[ArticleSource],
    *,
    editorial_revision: ProjectArticleRevision | None,
    language: str,
) -> tuple[ProjectArticleDraft, list[ArticleSource], bool]:
    previous_sources = _revision_sources(editorial_revision)
    changed = [
        item
        for item in sources
        if item.id not in previous_sources
        or previous_sources[item.id].get("content_hash") != item.content_hash
    ]
    candidates = changed or sources
    selected = sorted(
        candidates,
        key=lambda item: (
            {"repository_snapshot": 0, "project_journal": 1}.get(
                item.source_type, 2
            ),
            item.title.casefold(),
            item.id,
        ),
    )[:20]
    if not selected:
        raise ValueError("project article fallback has no citable source")
    lead_ids = [item.id for item in selected[:3]]
    if language == "ko":
        status_text = (
            "\uac80\uc99d\ub41c \uc2e0\uaddc \uadfc\uac70\uac00 "
            "\ucd94\uac00\ub418\uc5c8\uc9c0\ub9cc \uc790\ub3d9 \ud3b8\uc9d1 "
            "\uacb0\uacfc\uac00 \uac80\uc99d\uc744 \ud1b5\uacfc\ud558\uc9c0 "
            "\ubabb\ud574 \ub9c8\uc9c0\ub9c9 \uc815\uc0c1 \ubcf8\ubb38\uc744 "
            "\uc720\uc9c0\ud569\ub2c8\ub2e4."
        )
        section_title = "\uc5c5\ub370\uc774\ud2b8 \uc0c1\ud0dc"
    else:
        status_text = (
            f"{len(selected)} verified evidence items were added, but the generated "
            "edit failed validation, so the last valid article is preserved."
        )
        section_title = "Update status"
    status_section = {
        "key": "update_status",
        "title": section_title,
        "paragraphs": [
            {
                "sentences": [
                    {
                        "text": status_text,
                        "source_ids": [item.id for item in selected],
                    }
                ]
            }
        ],
    }
    allowed_source_ids = {item.id for item in sources}
    editorial_draft = _draft_from_revision(editorial_revision)
    preserved = (
        editorial_draft is not None
        and set(_used_source_ids(editorial_draft)).issubset(allowed_source_ids)
        and len(editorial_draft.sections) < 16
    )
    if preserved:
        assert editorial_draft is not None
        value = editorial_draft.model_dump(mode="json")
        value["sections"].append(status_section)
        draft = ProjectArticleDraft.model_validate(value)
    else:
        if language == "ko":
            lead = (
                f"{project}\uc758 \uadfc\uac70\ub294 "
                "\uc218\uc9d1\ub410\uc9c0\ub9cc \uc790\ub3d9 \ud3b8\uc9d1 "
                "\uac80\uc99d\uc774 \uc644\ub8cc\ub418\uc9c0 \uc54a\uc544 "
                "\uac80\uc99d\ub41c \uc0c1\ud0dc\ub9cc "
                "\ud45c\uc2dc\ud569\ub2c8\ub2e4."
            )
        else:
            lead = (
                f"Evidence for {project} was collected, but editorial validation "
                "did not complete; only the verified status is shown."
            )
        draft = ProjectArticleDraft.model_validate(
            {
                "title": f"{project} project overview",
                "standfirst": {
                    "sentences": [{"text": lead, "source_ids": lead_ids}]
                },
                "sections": [status_section],
            }
        )
    _validate_draft(draft, allowed_source_ids)
    return draft, selected, preserved


def _last_editorial_revision(
    session: Session,
    article: ProjectArticle,
) -> ProjectArticleRevision | None:
    return session.scalar(
        select(ProjectArticleRevision)
        .where(
            ProjectArticleRevision.article_id == article.id,
            ProjectArticleRevision.provider != "deterministic-fallback",
        )
        .order_by(ProjectArticleRevision.revision_number.desc())
        .limit(1)
    )


def refresh_project_article_fallback(
    session: Session,
    *,
    project: str,
    provider: GenerationProvider,
    settings: Settings,
    generation_error: Exception,
) -> dict[str, Any]:
    """Publish only deterministic, cited freshness metadata after model failure."""
    now = datetime.now(timezone.utc)
    sources = collect_project_sources(session, project)
    if not sources:
        raise ValueError("project article fallback has no sources")
    source_hash = _source_hash(sources)
    article = session.scalar(
        select(ProjectArticle).where(ProjectArticle.project_key == project)
    )
    if article is None:
        article = ProjectArticle(project_key=project)
        session.add(article)
        session.flush()
    current_revision = (
        session.get(ProjectArticleRevision, article.current_revision_id)
        if article.current_revision_id
        else None
    )
    if (
        current_revision is not None
        and current_revision.source_hash == source_hash
        and current_revision.change_summary_json.get("deterministic_fallback") is True
        and current_revision.change_summary_json.get("fallback_version")
        == _FALLBACK_VERSION
    ):
        article.status = "degraded"
        article.last_compared_at = now
        article.updated_at = now
        return {
            "project": project,
            "status": "fallback_unchanged",
            "revision": current_revision.revision_number,
            "error_type": type(generation_error).__name__,
        }
    editorial_revision = _last_editorial_revision(session, article)
    draft, selected, preserved = _fallback_draft(
        project,
        sources,
        editorial_revision=editorial_revision,
        language=settings.knowledge_content_language,
    )
    current_catalog = {
        item.id: item.catalog(include_excerpt=True) for item in sources
    }
    used_ids = _used_source_ids(draft)
    numbered_sources = []
    for citation_number, source_id in enumerate(used_ids, start=1):
        value = dict(current_catalog[source_id])
        value["citation_number"] = citation_number
        numbered_sources.append(value)
    next_revision = int(
        session.scalar(
            select(func.coalesce(func.max(ProjectArticleRevision.revision_number), 0))
            .where(ProjectArticleRevision.article_id == article.id)
        )
        or 0
    ) + 1
    revision = ProjectArticleRevision(
        article_id=article.id,
        revision_number=next_revision,
        previous_revision_id=current_revision.id if current_revision else None,
        title=draft.title,
        standfirst_json=draft.standfirst.model_dump(mode="json"),
        sections_json=[item.model_dump(mode="json") for item in draft.sections],
        sources_json=numbered_sources,
        source_manifest_json=[
            item.catalog(include_excerpt=False) for item in sources
        ],
        source_hash=source_hash,
        provider="deterministic-fallback",
        model=provider.model,
        model_digest=settings.generation_model_digest or "unresolved",
        prompt_version=settings.project_article_prompt_version,
        change_summary_json={
              "deterministic_fallback": True,
              "fallback_version": _FALLBACK_VERSION,
            "generation_error_type": type(generation_error).__name__,
            "generation_error": str(generation_error)[:500],
            "total_sources": len(sources),
            "cited_sources": len(numbered_sources),
            "fallback_items": len(selected),
            "preserved_editorial_revision": (
                  editorial_revision.revision_number
                  if preserved and editorial_revision is not None
                  else None
            ),
        },
        created_at=now,
    )
    session.add(revision)
    session.flush()
    article.current_revision_id = revision.id
    article.source_hash = source_hash
    article.status = "degraded"
    article.last_compared_at = now
    article.updated_at = now
    path = _write_managed_article(
        session,
        project=project,
        draft=draft,
        revision=revision,
        settings=settings,
    )
    session.flush()
    return {
        "project": project,
        "status": "fallback_updated",
        "revision": next_revision,
        "sources": len(sources),
        "cited_sources": len(numbered_sources),
        "path": str(path),
        "error_type": type(generation_error).__name__,
    }


def project_keys(
    session: Session,
    settings: Settings | None = None,
) -> list[str]:
    return [
        item.project
        for item in project_article_plan(session, settings or get_settings())
    ]


def refresh_all_project_articles(
    session: Session,
    *,
    provider: GenerationProvider,
    settings: Settings,
    only_project: str | None = None,
) -> list[dict[str, Any]]:
    projects = (
        [only_project]
        if only_project
        else [
            item.project
            for item in project_article_plan(session, settings)
            if item.due_reason is not None
        ]
    )
    results = []
    for project in projects[: settings.project_article_max_projects_per_run]:
        now = datetime.now(timezone.utc)
        article = session.scalar(
            select(ProjectArticle).where(ProjectArticle.project_key == project)
        )
        if article is None:
            article = ProjectArticle(project_key=project)
            session.add(article)
        article.status = "processing"
        article.last_compared_at = now
        article.updated_at = now
        session.commit()
        try:
            result = refresh_project_article(
                session,
                project=project,
                provider=provider,
                settings=settings,
            )
            session.commit()
            results.append(result)
        except Exception as exc:
            session.rollback()
            logger.exception(
                "project_article_refresh_failed",
                project=project,
                error_type=type(exc).__name__,
            )
            try:
                fallback = refresh_project_article_fallback(
                    session,
                    project=project,
                    provider=provider,
                    settings=settings,
                    generation_error=exc,
                )
                session.commit()
                results.append(fallback)
            except Exception as fallback_exc:
                session.rollback()
                article = session.scalar(
                    select(ProjectArticle).where(ProjectArticle.project_key == project)
                )
                if article is None:
                    article = ProjectArticle(project_key=project)
                    session.add(article)
                article.status = "error"
                article.last_compared_at = datetime.now(timezone.utc)
                article.updated_at = article.last_compared_at
                session.commit()
                logger.exception(
                    "project_article_fallback_failed",
                    project=project,
                    error_type=type(fallback_exc).__name__,
                )
                results.append(
                    {
                        "project": project,
                        "status": "failed",
                        "error_type": type(fallback_exc).__name__,
                        "error": str(fallback_exc)[:500],
                        "generation_error_type": type(exc).__name__,
                    }
                )
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.project_article_enabled:
        print(json.dumps({"status": "disabled"}))
        return 0
    provider = build_generation_provider(settings)
    if provider is None:
        raise RuntimeError("project article editing requires a generation provider")
    with SessionLocal() as session:
        results = refresh_all_project_articles(
            session,
            provider=provider,
            settings=settings,
            only_project=args.project,
        )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
