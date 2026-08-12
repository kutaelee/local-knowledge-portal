import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

SUPPORTED_EXTENSIONS = {
    ".md",
    ".mdx",
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".properties",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".sql",
    ".sh",
    ".ps1",
}
CODE_EXTENSIONS = SUPPORTED_EXTENSIONS - {
    ".md",
    ".mdx",
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".properties",
}

RETRIEVAL_FRONTMATTER_FIELDS = (
    "project",
    "tags",
    "category",
    "case_id",
    "case_revision",
    "evidence_gate",
    "knowledge_value_tier",
    "knowledge_value_labels",
    "lifecycle_status",
    "last_verified_at",
)


@dataclass(slots=True)
class Chunk:
    index: int
    kind: str
    content: str
    start_line: int
    end_line: int
    heading_path: str | None = None
    symbol_name: str | None = None
    language: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _split_long(chunk: Chunk, max_chars: int = 6000, overlap_lines: int = 8) -> list[Chunk]:
    if len(chunk.content) <= max_chars:
        return [chunk]
    lines = chunk.content.splitlines()
    result: list[Chunk] = []
    cursor = 0
    while cursor < len(lines):
        end = cursor
        size = 0
        while end < len(lines) and size + len(lines[end]) + 1 <= max_chars:
            size += len(lines[end]) + 1
            end += 1
        if end == cursor:
            oversized = lines[cursor]
            for start_char in range(0, len(oversized), max_chars):
                end_char = min(len(oversized), start_char + max_chars)
                result.append(
                    Chunk(
                        index=0,
                        kind=chunk.kind,
                        content=oversized[start_char:end_char],
                        start_line=chunk.start_line + cursor,
                        end_line=chunk.start_line + cursor,
                        heading_path=chunk.heading_path,
                        symbol_name=chunk.symbol_name,
                        language=chunk.language,
                        metadata={
                            **chunk.metadata,
                            "oversized_line_segment": True,
                            "start_char": start_char,
                            "end_char": end_char,
                        },
                    )
                )
            cursor += 1
            continue
        result.append(
            Chunk(
                index=0,
                kind=chunk.kind,
                content="\n".join(lines[cursor:end]),
                start_line=chunk.start_line + cursor,
                end_line=chunk.start_line + end - 1,
                heading_path=chunk.heading_path,
                symbol_name=chunk.symbol_name,
                language=chunk.language,
                metadata=chunk.metadata,
            )
        )
        cursor = max(end - overlap_lines, cursor + 1)
    return result


def chunk_markdown(text: str, max_chars: int = 6000) -> tuple[list[Chunk], dict]:
    parsed = frontmatter.loads(text)
    metadata = dict(parsed.metadata)
    retrieval_metadata = {
        key: metadata[key] for key in RETRIEVAL_FRONTMATTER_FIELDS if metadata.get(key) is not None
    }
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    starts: list[int] = [1]
    paths: list[str | None] = [None]
    for number, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        level, title = len(match.group(1)), match.group(2)
        headings[:] = [item for item in headings if item[0] < level]
        headings.append((level, title))
        starts.append(number)
        paths.append(" / ".join(item[1] for item in headings))
    starts.append(len(lines) + 1)
    chunks: list[Chunk] = []
    for i in range(len(starts) - 1):
        start, end = starts[i], starts[i + 1] - 1
        content = "\n".join(lines[start - 1 : end]).strip()
        if not content:
            continue
        chunks.extend(
            _split_long(Chunk(0, "markdown_section", content, start, end, paths[i]), max_chars)
        )
    for index, chunk in enumerate(chunks):
        chunk.index = index
        chunk.metadata = {**chunk.metadata, **retrieval_metadata}
    return chunks, metadata


SYMBOL_PATTERNS = [
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function|class|interface|type|fn)\s+([A-Za-z_]\w*)"
    ),
    re.compile(
        r"^\s*(?:public|private|protected|static|async|\s)*(?:[\w<>\[\],?]+\s+)+([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|:)"
    ),
]


def chunk_code(text: str, extension: str, max_chars: int = 6000) -> list[Chunk]:
    lines = text.splitlines()
    symbols: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        for pattern in SYMBOL_PATTERNS:
            if match := pattern.match(line):
                symbols.append((number, match.group(1)))
                break
    if not symbols:
        symbols = [(1, Path("file" + extension).stem)]
    symbols.append((len(lines) + 1, ""))
    language = extension.lstrip(".")
    chunks: list[Chunk] = []
    for index, ((start, name), (next_start, _)) in enumerate(
        zip(symbols, symbols[1:], strict=False)
    ):
        raw = Chunk(
            index=index,
            kind="code_symbol",
            content="\n".join(lines[start - 1 : next_start - 1]),
            start_line=start,
            end_line=max(start, next_start - 1),
            symbol_name=name,
            language=language,
            metadata={"symbol_type": "symbol"},
        )
        chunks.extend(_split_long(raw, max_chars))
    for index, chunk in enumerate(chunks):
        chunk.index = index
    return chunks


def chunk_document(path: Path, text: str) -> tuple[list[Chunk], dict]:
    extension = path.suffix.lower()
    if extension in {".md", ".mdx"}:
        return chunk_markdown(text)
    if extension in CODE_EXTENSIONS:
        return chunk_code(text, extension), {}
    lines = text.splitlines()
    chunks: list[Chunk] = []
    for start in range(0, len(lines), 100):
        section = "\n".join(lines[start : start + 100])
        chunks.append(
            Chunk(len(chunks), "line_window", section, start + 1, min(start + 100, len(lines)))
        )
    return chunks, {}
