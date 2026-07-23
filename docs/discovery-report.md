# Discovery report

Generated: 2026-07-23 (Asia/Seoul)

## Safety boundary

Discovery was read-only. No file was created in, moved from, renamed in, or deleted from an existing project or backup tree. The new repository is isolated at `C:\Dev\Repos\local-knowledge-portal`. In particular, `C:\Dev\Repos\local-voice-agent` and every other existing repository remain untouched.

## Drives and capacity

| Drive | Intended role | Used | Free |
|---|---|---:|---:|
| C: | source repository and development | 179.2 GB | 1,682.7 GB |
| E: | FireCuda operational data | 413.4 GB | 3,312.6 GB |
| D: | append-only backup destination | 379.1 GB | 551.6 GB |

The workstation layout policy identifies `C:\Dev\Repos` as the canonical Windows-native repository root, E: as active data storage, and D: as backup-only.

## Project candidates

`C:\Dev\Repos` contained 26 top-level project directories and 15 Git repositories at discovery time. After default exclusions, the tree contained approximately 19,157 files, of which 9,854 supported text/code files totalled 210,117,994 bytes (0.196 GB). Larger candidates by supported file count included CRM (2,996), dride-proxy (1,306), haircamera (1,162), dance_challenge (1,015), callme (649), and Interstellar_Drift (609).

The applied candidate root is `C:\Dev\Repos`, read-only, with `local-knowledge-portal/**` excluded to prevent self-indexing.

## Obsidian discovery

No directory containing `.obsidian` was found within the bounded discovery locations (`C:\Users\kutae\Documents`, `C:\Dev\Repos`, `E:\Workspace`, `E:\AI`, or `E:\LocalKnowledgePortal`, depth <= 3). The fallback vault is therefore `E:\LocalKnowledgePortal\vault`. Automated output is restricted to its `_generated` child.

## Exclusions

`.git`, `node_modules`, `.next`, `dist`, `build`, `coverage`, `target`, `bin`, `obj`, `.venv`, `venv`, `__pycache__`, `.cache`, `.idea`, `.vscode`, `vendor`, `tmp`, and `temp` are excluded by default. Binary files, files over 10 MiB, inaccessible paths, path escapes, and symlink/junction traversal are rejected or skipped.

## Applied layout

- Repository: `C:\Dev\Repos\local-knowledge-portal`
- Runtime data: `E:\LocalKnowledgePortal`
- PostgreSQL data: `E:\LocalKnowledgePortal\postgres`
- Models/cache/ingest/logs: corresponding E: subdirectories
- Fallback vault: `E:\LocalKnowledgePortal\vault`
- Backups: `D:\Backups\LocalKnowledgePortal`

The committed configuration contains examples only. Local operational configuration is gitignored.

## Risks and confirmations

- D: already contains protected historical content. Backup jobs only create new timestamped directories below the dedicated target and never mirror or prune.
- E: contains protected `backup` and `transfer` trees. They are outside all configured roots and are never scanned or changed.
- Repository scans may encounter junctions, permissions, or transient build output. The scanner does not follow reparse points and reconciliation records failures.
- Ollama and the requested embedding model were not detected during discovery; readiness reports this separately and ingestion can be tested with the deterministic test provider.
- The PostgreSQL image is pinned to pgvector 0.8.2 for PostgreSQL 18, including its OCI digest.
