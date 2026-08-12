# ADR 0007: Purpose-scoped repository retrieval

Status: accepted

## Context

The initial repository source root used its first relative directory as `project_key`. Repositories
below `/home/kutae/src/ai` therefore appeared as one project named `ai`. The same pipeline also
embedded every supported code file and, after the first value filter, every Markdown file. This
produced 2,311 file rows, 21,023 chunks, and a 1,509-job initial backlog that the portal described
as if all rows were human knowledge.

That representation was technically searchable but did not match the product purpose. Source code
is useful for exact path, symbol, error, and implementation lookup; it is not automatically a
reusable knowledge statement. Vendored repositories and dependency documentation are even less
appropriate as default RAG context.

## Decision

Repository ingestion has three independent scopes:

1. **Catalog scope** retains supported source files, immutable versions, chunks, paths, symbols,
   and operational history.
2. **Lexical scope** makes catalog content available to keyword, path, phrase, and symbol search.
3. **Semantic scope** defaults to project-owned Markdown and managed Vault documents. Code and
   nested Git dependencies remain lexical-only.

`LKP_REPOSITORY_EMBEDDING_MODE` controls the repository semantic scope:

- `docs_only` is the safe default.
- `lexical_only` disables repository vectors.
- `code_and_docs` is an explicit opt-in for full repository vectors.

The closest top-level Git repository below a source root defines the project. Nested Git
repositories are treated as dependencies of that project for navigation and are excluded from
semantic retrieval in `docs_only` mode. `document.relative_path` remains the source-root-relative
provenance path; `document.project_relative_path` is the path shown below the project node.

One file remains one durable queue job. This is the correct lease, idempotency, retry, version, and
provenance boundary. Resource scheduling is separated instead of coarsening the job:

- semantic jobs retain the conservative Ollama cooldown and burst limit;
- lexical jobs use a short cooldown and a larger burst under the worker's one-CPU cgroup limit;
- Ollama is capped at one CPU, one concurrent request, batch size one, and a bounded queue.

The overview labels rows as indexed files and search chunks. It separately reports knowledge
documents, code files, support files, semantic coverage, initial-scan backlog, and live changes.

## Migration and safety

Migration `0004_project_semantic_scope` adds `project_relative_path` without deleting rows.
`lkp_indexer.purpose_migration` supports dry-run and apply modes, records an atomic manifest, and
removes only rebuildable vectors that are outside the selected semantic policy. Source files,
documents, versions, chunks, jobs, and audit history remain intact.

The production conversion requires an immutable logical backup and successful restore test first.
Changing the policy or model requires an explicit pipeline revision and reindex.

## Consequences

- Explorer reflects real repository names instead of an incidental parent directory.
- Code remains discoverable and citable without consuming embedding compute by default.
- Semantic and RAG results have less dependency and generated-content noise.
- Initial scan backlog drains without invoking Ollama for lexical jobs.
- Full-code semantic search remains available as a deliberate, observable configuration choice.
