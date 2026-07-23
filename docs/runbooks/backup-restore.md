# Backup and restore runbook

Run `scripts/backup.ps1`. It creates a new timestamped directory, a PostgreSQL custom-format dump, SHA-256, database/schema/pgvector versions, and the source configuration hash. It never overwrites or prunes an existing backup.

Models and regenerable caches are excluded. The script copies portal-managed Vault pages,
raw hook event envelopes, non-secret configuration, and model/pipeline manifests into matching
immutable timestamp directories. It records environment variable names but never their values.
Source repositories are not backup payloads.

Validate a dump with:

```powershell
.\scripts\restore-test.ps1 -BackupDirectory `
  D:\Backups\LocalKnowledgePortal\database\YYYY-MM-DDTHHMMSS
```

The script verifies SHA-256, restores into a uniquely named temporary database, checks revision/counts, verifies that no foreign keys are unvalidated, performs a content query, and only then reports success. The dedicated temporary database is removed in `finally`; the immutable backup remains.

For a real recovery, restore into a new database name, validate it, stop API/workers, change `LKP_DATABASE_URL`, and start services. Never restore over the active database.
