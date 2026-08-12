# WSL2 Docker transition report

Date: 2026-07-23, Asia/Seoul

Status: **VERIFIED**

## Active layout

| Purpose | Active path |
|---|---|
| Linux-native repository | `/home/kutae/src/local-knowledge-portal` |
| Compose, config, secret env | `C:\Docker\local-knowledge-portal` |
| Docker Desktop distribution and named volumes | `C:\Docker\DockerDesktop` |
| Runtime data, spool, vault, exports | `E:\Data\LocalKnowledgePortal` |
| Ollama model store | `E:\AI\Models\Ollama` |
| Immutable backup | `D:\LocalBackup\LocalKnowledgePortal` |

The old `C:\Dev\Repos\local-knowledge-portal` checkout, `E:\LocalKnowledgePortal` data, and
`D:\Backups\LocalKnowledgePortal` backups were not moved, overwritten, or deleted. Windows-native
API, web, indexer, collector, and old PostgreSQL were stopped before the Docker cutover.

## Running services

The stack was launched from Ubuntu WSL2 with the active Compose file under `C:\Docker`.

- PostgreSQL 18.4 + pgvector 0.8.2: healthy, private Docker network only.
- Alembic migration: exited 0 at `0002_activity_knowledge`.
- Ollama 0.32.1: healthy, private Docker network only.
- `qwen3-embedding:0.6b`: pulled to `E:\AI\Models\Ollama`, digest prefix `ac6da0dfba84`.
- API: healthy on `127.0.0.1:8010`.
- Web: HTTP 200 on `127.0.0.1:3010`.
- Worker: running and embedding.
- Watcher and reconciler: running with healthy heartbeat rows.
- Hook collector: running.

Windows listeners for ports 8010 and 3010 are owned by `com.docker.backend`; no active
Windows-native portal process owns those ports.

## Executed verification

- `/health/ready`: database true, schema `0002_activity_knowledge`, Ollama true.
- Browser smoke test: title `Local Knowledge Portal`, overview heading visible, zero console
  errors.
- Read-only source mount validation container: ruff passed and 19 unit tests passed.
- A standalone Windows hook event was atomically written to
  `E:\Data\LocalKnowledgePortal\ingest\codex-spool`, secret text was redacted, and the Docker
  collector imported it as an `UNVERIFIED` activity.
- Initial WSL source reconciliation queued files below `/home/kutae/src` while excluding this
  portal repository.
- Hybrid search for `3D model texture generation` returned high confidence, a vector similarity
  of `0.6815034963493521`, and provenance under `/home/kutae/src/ai/TRELLIS.2/app.py`.
- Immutable backup:
  `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T115128Z\lkp.dump`.
- Backup size: 7,478,851 bytes.
- Backup SHA-256:
  `d6d89bfe88c7e0b8f488d7a5e76e8830313972c2894b29e20e77264085991d00`;
  independently recomputed checksum matched.
- Ephemeral Docker restore test passed at revision `0002_activity_knowledge` with 128 documents,
  1,303 chunks, 1,303 vectors, and 1 activity. The temporary restore container was removed.

## Failures corrected during transition

- The first migration rejected the private Docker hostname `ollama`. The security guard now
  permits only loopback names or the exact private service name `ollama`; rebuild and migration
  then passed.
- The watcher initially referenced the WSL host path `/mnt/e/.../vault`, which was not mounted at
  that path in the container. The operational root was corrected to `/data/vault`; the watcher
  remained up and heartbeat rows became healthy.
- The first backup attempt sourced a CRLF `.env` in Bash, changing the Compose project name. The
  script now parses only required values and strips carriage returns.
- A second backup attempt used archive-preserving copy flags unsupported by the D: drvfs mount.
  Configuration copy now avoids metadata preservation; backup and restore both passed.
- The first Linux unit run exposed Windows-path parsing and a stale validator-message assertion.
  Cross-platform workspace parsing and the message were corrected; ruff and all 19 unit tests
  passed in the rerun.

No failure above was reported as success before its corrected rerun.
