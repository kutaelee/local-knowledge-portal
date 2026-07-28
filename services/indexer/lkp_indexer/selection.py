from fnmatch import fnmatchcase
from pathlib import Path

from lkp.models import SourceRoot

from .projects import git_repository_roots

CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
}
SEMANTIC_DOCUMENT_EXTENSIONS = {".md", ".mdx"}
REPOSITORY_SOURCE_TYPES = {"repositories", "repository_collection"}
SEMANTIC_POLICY_VERSION = "purpose-aware-v3"


def _semantic_exclusion_reason(path: Path, source_root: SourceRoot) -> str | None:
    """Match configured semantic-only exclusions against a root-relative path.

    This is deliberately separate from scanner ignore rules. A matched file is
    still versioned, chunked and available to exact/path/lexical retrieval; it
    merely does not consume scarce embedding capacity.
    """

    try:
        relative = path.resolve(strict=False).relative_to(
            Path(source_root.canonical_path).resolve(strict=False)
        ).as_posix()
    except ValueError:
        return "semantic_path_outside_root"
    for configured in source_root.semantic_exclude_patterns or []:
        pattern = str(configured).replace("\\", "/").lstrip("./")
        if pattern and fnmatchcase(relative, pattern):
            return "semantic_exclude_pattern"
    return None


def semantic_policy(
    path: Path,
    source_root: SourceRoot,
    *,
    repository_mode: str,
) -> tuple[bool, str | None]:
    extension = path.suffix.casefold()
    exclusion_reason = _semantic_exclusion_reason(path, source_root)
    if exclusion_reason:
        return False, exclusion_reason
    if source_root.source_type == "obsidian":
        return extension in SEMANTIC_DOCUMENT_EXTENSIONS, (
            None
            if extension in SEMANTIC_DOCUMENT_EXTENSIONS
            else "obsidian_non_markdown"
        )
    if source_root.source_type not in REPOSITORY_SOURCE_TYPES:
        return True, None
    if repository_mode == "code_and_docs":
        return True, None
    if len(
        git_repository_roots(path, Path(source_root.canonical_path))
    ) > 1:
        return False, "nested_repository_dependency"
    if repository_mode == "lexical_only":
        return False, "repository_lexical_only"
    if extension in SEMANTIC_DOCUMENT_EXTENSIONS:
        return True, None
    return False, "repository_docs_only"
