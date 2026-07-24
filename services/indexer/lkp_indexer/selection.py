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


def semantic_policy(
    path: Path,
    source_root: SourceRoot,
    *,
    repository_mode: str,
) -> tuple[bool, str | None]:
    extension = path.suffix.casefold()
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
