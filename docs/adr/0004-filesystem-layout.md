# ADR 0004: Workstation filesystem layout

Status: accepted

The repository is Linux-native and lives in the WSL ext4 filesystem at
`/home/kutae/src/local-knowledge-portal`. Operational Compose configuration,
ignored secrets, and the stable Windows hook wrapper live under
`C:\Docker\local-knowledge-portal`.

Application runtime data, ingest artifacts, logs, managed vault content, cache,
and exports live under `E:\Data\LocalKnowledgePortal`. Ollama embedding models
live under `E:\AI\Models\Ollama`. PostgreSQL/pgvector uses the Docker-managed
named volume `local-knowledge-portal_postgres-data` inside Docker Desktop's C:
VHDX and is backed up through PostgreSQL logical dumps rather than filesystem
copies. Append-only dated backups live under
`D:\LocalBackup\LocalKnowledgePortal`.

The application containers receive these paths through environment variables
and read-only or explicit writable mounts. Existing repositories and protected
D:/E: trees remain outside the write allowlist.
