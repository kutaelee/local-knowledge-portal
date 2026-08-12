# ADR 0017: Single workstation Ollama runtime

- Status: Accepted
- Date: 2026-07-26

## Context

Hermes used Windows Ollama while portal embedding and knowledge editing could
start separate Docker Ollama daemons. The daemons had different model stores,
were reported as separate GPU workloads, and could load equivalent models at
the same time. GPU admission and operator stop controls therefore did not have
one truthful runtime boundary.

## Decision

One loopback-only Windows Ollama daemon at `127.0.0.1:11434` owns the active
`E:\AI\Models\Ollama\generation\models` store. Hermes, portal embedding, and
portal knowledge editing use it. Portal containers connect only through Docker
Desktop's `host.docker.internal:11434` gateway; the settings validator allows
that exact local boundary and rejects remote hosts, HTTPS, paths, queries, and
fragments.

The former Compose Ollama services are behind the disabled `legacy-ollama`
profile and remain stopped. Their data is retained for rollback. Existing
embedding blobs were registered in the active store using verified same-volume
hardlinks, so no model was downloaded or byte-copied.

GPU-heavy curation and embedding reindex commands still enter `gpuq`. Their
one-shot application containers call the Windows daemon after admission and
must never start an Ollama container. The dashboard reports one allowlisted
external runtime and can unload only model names returned by that runtime.

## Consequences

- There is one model list, one loaded-model state, and one operator stop
  boundary for Hermes and the portal.
- A portal query can load an embedding model into GPU memory; short keep-alive
  and GPUQ admission for batch work limit retention and contention.
- Stopping the daemon affects all three consumers, so normal controls unload
  models rather than terminating the daemon.
- Rollback requires stopping Windows Ollama before enabling the legacy Compose
  profile; two Ollama daemons must not run concurrently in normal operation.
