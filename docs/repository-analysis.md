# Repository analysis module

## Purpose

`lkp_indexer.repository_analysis` extends Local Knowledge Portal with read-only repository
intelligence. It records paths, hashes, symbols, relations, configuration keys, dependency
declarations, claims, validation results, and synthesized support knowledge. It never stores whole
source files, decompiler output, configuration values, secrets, or model prompt source text.

The module is additive. It reuses the portal PostgreSQL service, FastAPI process, Next.js UI,
provenance conventions, and existing document/embedding foreign key. It does not create another
database, vector service, queue product, or frontend framework.

## Architecture

```text
allowlisted read-only repository
  -> deterministic discovery and source fingerprint
  -> language/build detection
  -> analyzer plugins
  -> bounded analysis units and optional localhost model
  -> claims
  -> source/hash/line/symbol/config/dependency validator
  -> searchable knowledge metadata
  -> PostgreSQL repository_* tables
  -> FastAPI Hermes/query endpoints
  -> /repository-analysis management console
```

The first analyzer set covers Python AST, bounded Java/JavaScript/TypeScript/Kotlin/shell/SQL
declarations and calls, XML elements, YAML/JSON/TOML/properties keys, and Maven/NPM/PyPI dependency
manifests. New analyzers implement `AnalyzerPlugin` and are added to `DEFAULT_PLUGINS`.

## Data model

Migration `0015_repository_analysis` adds:

- `repository_project`, keyed by an origin hash rather than a display name.
- immutable `repository_snapshot`, deduplicated by project and source hash.
- `repository_source_file`, `repository_source_symbol`, `repository_source_relation`.
- `repository_configuration_reference`, `repository_dependency_artifact`.
- `repository_analysis_job`, `repository_analysis_task`, `repository_analysis_claim`.
- `repository_knowledge_item`, with an optional link to the existing `document` table.
- `repository_evaluation_case`, `repository_evaluation_result`.

Migration `0018_repository_checkpoints` adds non-searchable, restart-safe staging:

- `repository_analysis_checkpoint` identifies one run by project, source hash, analysis version,
  model identity, prompt version, output limits, and claim schema.
- `repository_analysis_task_checkpoint` commits each terminal analysis task independently.
- A restarted run restores only tasks whose unit name and exact source-file list still match the
  current deterministic plan. Changed source, model, prompt, limits, or schema produces a different
  checkpoint and cannot reuse stale output.
- Task staging stores bounded derived claims, evidence paths/hashes/lines, counters, and failure
  state. It has no source-text, prompt, or raw-model-response column.
- Staged rows are never searched. Only a fully validated final Snapshot is promoted through the
  normal repository persistence path; the checkpoint is then marked `PROMOTED`.

General search is allowlisted to `SOURCE_VERIFIED`, `TEST_VERIFIED`,
`RUNTIME_VERIFIED`, `HUMAN_APPROVED`, plus the explicitly conditional
`PARTIALLY_VERIFIED` and `ADDITIONAL_DATA_NEEDED` states. Rejected, stale,
contradictory, unresolved, manual-review, runtime-evidence, and additional-analysis
states have `searchable=false`. Queries default to the newest non-stale snapshot.

## Run a read-only analysis

Run inside the WSL repository or application container. The source must be under an explicit
allowlist. Start with a small module before a whole repository:

```bash
uv run python -m lkp_indexer.repository_analysis \
  /home/kutae/src/local-knowledge-portal/services/indexer/lkp_indexer/repository_analysis \
  --allowed-root /home/kutae/src/local-knowledge-portal
```

The command prints only IDs, hashes, metrics, warnings, and persistence state. Add
`--database-url` only for a migrated non-production database or after an explicitly approved
production change.

The optional local model is disabled by default. Set the `REPO_ANALYSIS_MODEL_*` variables and
pass `--enable-local-model`. Only `localhost`, loopback, `host.docker.internal`, or the internal
`ollama` hostname is accepted; there is no cloud fallback.

For deployed Java products, prepare internal JAR evidence before analysis:

```powershell
.\scripts\prepare-repository-jars.ps1 `
  -SourceRoot C:\Dev\Repos\indigoesb\esb `
  -ProjectKey esb
```

The script hashes every JAR, identifies organization packages, decompiles each unique artifact
with the pinned Vineflower tool, and creates same-volume hard-linked project views under the
rebuildable repository-analysis cache. Source repositories remain read-only. The manifest records
the original JAR path and digest; neither raw JARs nor full decompiled source are stored in
PostgreSQL.

Derived evidence is supplied with a path prefix:

```bash
uv run python -m lkp_indexer.repository_analysis \
  /sources/windows-repositories/indigoesb/esb \
  --allowed-root /sources/windows-repositories/indigoesb/esb \
  --evidence-root .decompiled=/data/cache/repository-analysis/decompiled/projects/esb
```

Model work is deterministic and sequential. Each component, JAR, or derived-evidence set is
divided into batches of at most five files. The first attempt receives at most 4,500 source
characters and a retry receives at most 2,250; fair per-file allocation prevents a large file from
consuming the whole evidence window. Every failed batch is retried once. A second failure is
recorded as additional analysis required and does not stop later batches.

Files that cannot be supplied directly are converted only into rebuildable, hash-addressed
evidence outside the source tree. The IMC minified JavaScript is represented by verified
4,096-character text chunks, and the agent archive disguised as a properties file is represented
by its six path-validated XML members. The pipeline verifies the original digest and every
derived member or chunk digest before model use, and stores neither the raw source nor the full
derived text in PostgreSQL. Before GPU submission, `scripts/preflight-repository-prompts.py`
tokenizes every planned production prompt with the canonical Qwen3.6 tokenizer. The run is
admissible only when the largest input plus the reserved 2,048 output tokens fits the qualified
16,384-token context. The current IndigoESB evidence and exact counts are recorded in
`docs/evidence/indigoesb-qwen-prompt-preflight.md`.

The evaluator uses the 15 required support categories: entry point, call flow, implementation
selection, configuration priority, exception conditions, retry/timeout, transaction,
DB/message flow, concurrency/state, dependency usage, missing-JAR impact, log location, candidate
causes, additional evidence, and change impact. An analysis run first verifies each case against
the exact file/hash/line/symbol evidence. After persistence, the GPU post-process creates missing
repository knowledge embeddings and prepares a latest-Snapshot hybrid retrieval package. A final
Qwen run answers from only those retrieved knowledge items. It validates the answer schema and
rejects citations that were not retrieved or do not match the stored source metadata.

## Qwen3.6 27B MTP readiness

Repository reasoning may use the workstation's existing vLLM 0.25.1 runtime and canonical
`Qwen3.6-27B-Q4_K_M.gguf` artifact. GGUF loading uses the official
`vllm-gguf-plugin==0.0.4` with `gguf==0.19.0`. Do not install a second vLLM runtime, duplicate
weights, or start the model outside `gpuq`.

The qualified low-concurrency launch shape is:

```bash
python scripts/vllm-gguf-mtp-launcher.py serve /canonical/Qwen3.6-27B-Q4_K_M.gguf \
  --served-model-name qwen3.6-27b-mtp-q4-k-m \
  --tokenizer Qwen/Qwen3.6-27B \
  --hf-config-path Qwen/Qwen3.6-27B \
  --load-format gguf \
  --language-model-only \
  --reasoning-parser qwen3 \
  --speculative-config '{"method":"mtp","num_speculative_tokens":1}' \
  --no-enable-prefix-caching \
  --enforce-eager \
  --kv-cache-memory-bytes 1350000000 \
  --max-model-len 14000 \
  --max-num-batched-tokens 256 \
  --max-num-seqs 1
```

Qwen3.6 shares the Qwen3.5 MTP implementation in the installed vLLM runtime, so internal logs or
module names may identify `qwen3_5_mtp`. This is not a separate draft model. vLLM 0.25.1 checks
the positional model as Transformers JSON before its external GGUF parser can use
`--hf-config-path`; the repository launcher bypasses only that check for an existing local GGUF,
explicit MTP-1, disabled prefix caching, and explicit GGUF load format. The operational wrapper is
`scripts/run-gpu-repository-vllm.sh`; it is only valid as the argv workload of an admitted gpuq
reservation. `scripts/qualify-repository-vllm.sh` starts the server, verifies health and structured
JSON output, and shuts it down. Qualification does not analyze a repository or write knowledge.
Eager execution is intentional for the qualified WSL path. The installed runtime also carries
bounded compatibility guards for this exact artifact: each fused GGUF row resolves its real
Q4/Q5/Q6 quantization type from the packed width, hybrid Mamba cache pages align to the attention
page granularity, and the text-only class supplies the three equal M-RoPE position axes required
by Qwen3.6. The legacy V1 runner is used because WSL does not expose the unified virtual addressing
required by the V2 runner.

The qualified launch must leave room for one evidence batch and its result. Prompt preflight
measured a maximum input-plus-output requirement of 13,555 tokens, so a 14,000-token model limit
is sufficient. The 256-token batch cap uses chunked prefill to reduce transient activation and
warmup memory. The final qualification used an explicit 1,350,000,000-byte KV cache, observed
30,400 MiB peak total GPU usage, and stayed below the 30,559 MiB safety ceiling. Reserve 28,000 MiB
for this measured launch shape: `gpuq` reservations represent the workload delta, not the peak
total reported by the scheduler. The launcher omits `--gpu-memory-utilization` whenever the exact
KV cache byte count is provided because vLLM ignores utilization in that mode. Do not guess a
larger cache or increase the queue reservation without a new measured qualification.

Keep `REPO_ANALYSIS_MODEL_ENABLED=false` until qualification succeeds. Afterward, use
`REPO_ANALYSIS_MODEL_BASE_URL=http://host.docker.internal:18000/v1`,
`REPO_ANALYSIS_MODEL_NAME=qwen3.6-27b-mtp-q4-k-m`, and enable bounded transient source excerpts.
Prompts are not persisted. Every returned claim still passes the normal hash, file, line, symbol,
configuration, and dependency validator before it can become searchable knowledge.
Qwen3.6 thinking is disabled per request for bounded extraction, and vLLM constrained decoding
uses separate strict JSON Schemas for analysis claims and technical-support answers. A syntactically
valid object alone is not accepted as evidence.

Codex source substitution is disabled. A model request failure, rejected claim schema,
contradiction, explicitly reported missing knowledge, or claim-limit skip is recorded as an
unresolved task and the pipeline continues with the next task. Every task records
`codex_intervention_policy=RECORD_FAILURE_AND_CONTINUE`,
`codex_source_substitution=false`, and `failure_recorded_and_continued=true` when unresolved.
The manifest records `operator_intervention_required=false`, the unresolved count in
`analysis_failures_recorded`, and zero Codex intervention, source-read, substitution, and
authored-claim counts. No Codex-authored replacement Claim may become searchable knowledge.

Every terminal extraction task is checkpointed before the next task starts, including a task that
ends as `ADDITIONAL_ANALYSIS_REQUIRED`. If the process, container, vLLM server, or gpuq job stops,
rerun the same component command with the same source and analysis settings. The pipeline restores
the completed tasks and resumes at the first unfinished task. It reports
`checkpoint_restored_tasks` and `checkpoint_saved_tasks` in the bounded result metrics. Changing
the source hash or analysis fingerprint intentionally starts a fresh checkpoint. Evaluation and
final Snapshot promotion are still rerun after extraction recovery so a partial run cannot become
searchable.

After qualification, `scripts/analyze-indigo-component.sh {esb|imc|agent}` performs exactly one
component run: start the loopback-only model inside its gpuq reservation, prove the application
container can reach it, run the source-plus-decompiled-evidence analysis, persist only validated
facts and metadata, write a bounded result summary, and gracefully stop the model. Submit the
three components as separate gpuq jobs in `esb`, `imc`, `agent` order.

After all three runs, submit `scripts/run-gpu-repository-postprocess.sh` through `gpuq`. It uses
the workstation Ollama embedding model to create missing vectors and writes the source-free
hybrid retrieval package to the portal data root. Then submit
`scripts/evaluate-indigo-support.sh` through `gpuq`; it starts Qwen3.6 MTP-1, evaluates all 15
categories for each latest IndigoESB Snapshot, persists retrieval and answer-quality results, and
stops the model.

## API and UI

The API prefix is `/api/v1/repository-analysis`:

- `GET /projects`, `/projects/{id}`, `/projects/{id}/snapshots`.
- `GET /projects/{id}/status`.
- `GET /search` with project, snapshot, component, knowledge type, validation, and confidence
  filters.
- `GET /tools/{tool_name}` for architecture, component, logic, configuration, data/message flow,
  dependency/JAR, troubleshooting, and change-impact context.

Every knowledge response includes project, snapshot, validation status, confidence, source
references, and unknowns. The management console is `/repository-analysis` and is linked as
`레포 분석` in the existing sidebar.

## Security boundaries

- Source paths must be inside the supplied allowlist and are resolved before traversal.
- Source roots are read-only; the pipeline has no source mutation operation.
- Symlinks, binary files, oversized files, common secret filenames, and non-UTF-8 files are
  excluded with an observable reason.
- Configuration values and file contents do not appear in the manifest or database schema.
- Optional model calls are local-only, bounded, structured JSON, temperature zero, and have a
  fixed timeout/output limit. Bounded source excerpts are transient and disabled by default.
- Production migration, production analysis, existing knowledge mutation, external model calls,
  and large binary decompilation require separate approval.

## Rollback

1. Hide the sidebar link or stop routing the repository-analysis router.
2. Leave `REPO_ANALYSIS_MODEL_ENABLED=false`.
3. Do not downgrade or delete the additive tables automatically. They may contain reviewed
   evaluation evidence.
4. If a schema rollback is required, first take and verify a logical backup, restore it into a
   new test database, and use an explicitly reviewed migration.

The feature has no source-file rollback because it never writes to analyzed repositories.

## Known limitations

- The initial code analyzers are intentionally conservative and do not resolve full type systems,
  dynamic dispatch, reflection, runtime configuration precedence, or transaction boundaries.
- `repository_knowledge_item.document_id` is the integration seam for the existing chunk and
  embedding pipeline. Publication/materialization into managed documents must run under the
  existing knowledge quality gate; this module does not bypass it.
- Static evidence cannot establish deployed override values, runtime branch selection, live
  transaction outcomes, or production failure causes. Evaluations must preserve these as
  additional-data requirements unless runtime evidence is supplied.
- Retrieval and answer-quality metrics are evidence only after the post-persistence jobs have run
  against representative data; unit tests alone do not establish them.
