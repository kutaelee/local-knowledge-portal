# ADR 0004: Workstation filesystem layout

Status: accepted

Git and source code live at `C:\Dev\Repos\local-knowledge-portal`. Active database, models, cache, ingest artifacts, logs, vault, and exports live below `E:\LocalKnowledgePortal`. Backups are immutable timestamped directories below `D:\Backups\LocalKnowledgePortal`.

Paths are supplied through environment variables or YAML. No operational absolute path is embedded in application logic. Existing repositories and protected D:/E: trees are outside the write allowlist.
