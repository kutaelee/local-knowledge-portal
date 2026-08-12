from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from lkp.models import Document, DocumentLink, DocumentState
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

_WIKILINK = re.compile(r"\[\[([^\]\r\n]+)\]\]")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]\r\n]*\]\(([^)\r\n]+)\)")
_EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data", "file"}


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    raw_target: str
    link_type: str
    source_line: int


@dataclass(frozen=True, slots=True)
class DocumentCatalog:
    documents: tuple[Document, ...]
    by_path: dict[str, Document]
    by_stem: dict[str, tuple[Document, ...]]


def _clean_target(value: str, *, wikilink: bool) -> str | None:
    target = value.split("|", 1)[0] if wikilink else value
    target = target.strip().strip("<>").replace("\\", "/")
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme.casefold() in _EXTERNAL_SCHEMES or parsed.netloc:
        return None
    target = unquote(parsed.path).strip().lstrip("/")
    if not target:
        return None
    normalized = posixpath.normpath(target)
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized[:4000]


def extract_document_links(content: str) -> list[ExtractedLink]:
    """Extract Markdown links outside fenced code blocks with source lines."""

    links: list[ExtractedLink] = []
    fence: str | None = None
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        marker = (
            "```"
            if stripped.startswith("```")
            else "~~~"
            if stripped.startswith("~~~")
            else None
        )
        if marker:
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        for match in _WIKILINK.finditer(line):
            target = _clean_target(match.group(1), wikilink=True)
            if target:
                links.append(ExtractedLink(target, "wikilink", line_number))
        for match in _MARKDOWN_LINK.finditer(line):
            target = _clean_target(match.group(1), wikilink=False)
            if target:
                links.append(ExtractedLink(target, "markdown", line_number))
    return links


def build_document_catalog(session: Session, source_root_id) -> DocumentCatalog:
    documents = tuple(
        session.scalars(
            select(Document).where(
                Document.source_root_id == source_root_id,
                Document.state == DocumentState.active,
            )
        )
    )
    by_stem: dict[str, list[Document]] = {}
    for item in documents:
        by_stem.setdefault(PurePosixPath(item.filename).stem.casefold(), []).append(item)
    return DocumentCatalog(
        documents=documents,
        by_path={item.relative_path.casefold(): item for item in documents},
        by_stem={key: tuple(values) for key, values in by_stem.items()},
    )


def _resolve(
    source: Document,
    raw_target: str,
    link_type: str,
    catalog: DocumentCatalog,
) -> Document | None:
    target = raw_target.replace("\\", "/")
    candidates: list[str] = []
    if link_type == "markdown":
        base = source.parent_path if source.parent_path not in {"", "."} else ""
        relative = posixpath.normpath(posixpath.join(base, target))
        if relative == ".." or relative.startswith("../"):
            return None
        candidates.append(relative)
    else:
        candidates.extend([target, posixpath.normpath(posixpath.join(source.parent_path, target))])
    expanded: list[str] = []
    for candidate in candidates:
        expanded.append(candidate)
        if not PurePosixPath(candidate).suffix:
            expanded.extend([f"{candidate}.md", f"{candidate}.mdx"])
    for candidate in dict.fromkeys(expanded):
        if match := catalog.by_path.get(candidate.casefold()):
            return match
    if "/" not in target:
        stem = PurePosixPath(target).stem.casefold()
        matches = catalog.by_stem.get(stem, ())
        if len(matches) == 1:
            return matches[0]
    return None


def sync_document_links(
    session: Session,
    source: Document,
    content: str,
    *,
    catalog: DocumentCatalog | None = None,
) -> int:
    session.execute(
        delete(DocumentLink).where(DocumentLink.source_document_id == source.id)
    )
    if source.extension not in {".md", ".mdx"}:
        return 0
    catalog = catalog or build_document_catalog(session, source.source_root_id)
    extracted = extract_document_links(content)
    for item in extracted:
        target = _resolve(source, item.raw_target, item.link_type, catalog)
        session.add(
            DocumentLink(
                source_document_id=source.id,
                target_document_id=target.id if target else None,
                raw_target=item.raw_target,
                link_type=item.link_type,
                source_line=item.source_line,
            )
        )
    return len(extracted)


def resolve_links_for_target(session: Session, target: Document) -> int:
    """Resolve previously dangling links when their target arrives later."""

    candidates = {
        target.relative_path,
        str(PurePosixPath(target.relative_path).with_suffix("")),
        target.filename,
        PurePosixPath(target.filename).stem,
    }
    rows = list(
        session.scalars(
            select(DocumentLink)
            .join(Document, Document.id == DocumentLink.source_document_id)
            .where(
                DocumentLink.target_document_id.is_(None),
                Document.source_root_id == target.source_root_id,
                DocumentLink.raw_target.in_(candidates),
            )
            .limit(2000)
        )
    )
    resolved = 0
    catalog = build_document_catalog(session, target.source_root_id)
    sources = {item.id: item for item in catalog.documents}
    for row in rows:
        source = sources.get(row.source_document_id)
        if source is None:
            continue
        match = _resolve(source, row.raw_target, row.link_type, catalog)
        if match is not None:
            row.target_document_id = match.id
            resolved += 1
    return resolved
