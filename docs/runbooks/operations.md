# Operations runbook

## Start

1. Keep the Linux-native checkout at `~/src/local-knowledge-portal`.
2. Run `scripts/bootstrap-wsl-docker.ps1` from Windows once; review
   `C:\Docker\local-knowledge-portal\config`.
3. Run `./scripts/docker-stack.sh up` inside WSL.
4. Check `/health/live` and `/health/ready`. The stack starts PostgreSQL, migration, Ollama model
   pull, API, web, worker, watcher, reconciler, and hook collector in dependency order.

## Add a source root

Add an explicit existing directory to `config/source-roots.yaml`, keep `read_only: true`, and review
excludes. Never register an entire drive. Run `scripts/scan.ps1`; inspect queue counts before
starting many workers.

For many Windows repositories under `C:\Dev\Repos`, use the existing read-only
`/sources/windows-repositories` mount and `type: repository_collection`. This mode accepts only
direct child Git repositories and discovers new ones on the next bounded reconciliation. Keep
`watch_mode: disabled` for this Docker bind mount so repository count does not multiply polling
watchers. Copy the example entry into the active configuration only after reviewing which direct
children are safe to index; the mount alone does not activate collection.

## Codex activity capture

Run `scripts/install-codex-hook.ps1` to idempotently merge the six global activity hooks into
`%USERPROFILE%\.codex\hooks.json`. Existing Codex configuration is copied to a timestamped
directory under `D:\LocalBackup\LocalKnowledgePortal\config\codex` before the merge.

Each hook only calls `scripts/codex-hook.ps1`, which redacts and atomically spools a bounded JSON
envelope to `E:\Data\LocalKnowledgePortal\ingest\codex-spool\pending`. It does not depend on the portal,
API, or database. `scripts/start-hook-collector.ps1` imports raw envelopes idempotently and keeps
reported results separate from exit-code-backed verified results. Malformed, unsupported, and
oversized envelopes are quarantined; a fallback spool is replayed after recovery.

Only meaningful instructions, changed files, failures, verification/operation commands, and
reported outcomes become activity history. Session lifecycle, acknowledgements, and read-only
inspection events do not become durable rows. A successfully handled raw envelope is deleted; only
failed or unclaimed envelopes remain in the spool for retry. Ordinary retained activity is still
not promoted to a wiki page. Unlinked successful tool detail is marked `rolled_up` after the
configured 30-day window and hidden from the default activity list; prompts, outcomes, failures,
and evidence are retained. The bounded check runs hourly and does not delete DB rows. The collector
accepts structured `exit_code`/`return_code`, bounded `tool_output`, and a read-only transcript
fallback. A reported final answer is never used as command evidence.

The optional global `C:\Users\kutae\.codex\AGENTS.md` reuse memo is not a hook and does not
force output. In a new Codex session, an agent may add a compact
`재사용 메모: 상황 — … | 원인 — … | 조치 — … | 검증 — …` only when the statement is
evidence-bound and likely to recur. The collector treats it as candidate structure, never as proof.

### Read-only Codex retrieval

`python -m lkp_indexer.codex_mcp` is a bounded stdio MCP bridge to the loopback portal API. It does
not change hook behavior, start another API, access PostgreSQL directly, or write indexed data.
Global Codex configuration keeps the server optional, sets five-second startup and twelve-second
tool timeouts, and allowlists only `retrieve_context` and `get_source`.

The routing policy is deliberately source-first. Do not call the portal before one bounded current
source search unless the user explicitly requests portal evidence. A hybrid `confidence=high`
response can return at most five contexts totaling 6,000 characters. If the embedding circuit is
open or the response is low confidence, the bridge strips context bodies, returns at most five
240-character navigation hints, and keeps `no_answer=true`. Portal or MCP failure must not block
ordinary source inspection.

Validate the bridge without a persistent process:

```powershell
@'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
'@ | wsl.exe -d Ubuntu -- `
  /home/kutae/src/local-knowledge-portal/.venv/bin/python `
  -m lkp_indexer.codex_mcp

codex mcp list
```

Rollback is configuration-only: remove the `mcp_servers.local_knowledge` table and the matching
global routing paragraph, or restore their timestamped backup. No portal data migration is needed.

### Local Ollama/model conversation capture

Ollama does not expose a global completed-chat hook. Configure each local chat client to call the
portal callback after a complete response, keeping the project key explicit:

```powershell
.\scripts\capture-local-llm-chat.ps1 `
  -ProjectKey local-knowledge-portal `
  -Model qwen3.5:9b-q4_K_M `
  -UserMessage $prompt `
  -AssistantMessage $response `
  -SessionId $sessionId `
  -TurnId $turnId
```

The callback is localhost-only, size bounded, redacted, and written atomically to
`E:\Data\LocalKnowledgePortal\ingest\local-llm-spool`. It does not call the database or a model.
Collector import is idempotent. The UI deliberately lists these records under **로컬 모델 대화**,
not **Codex 작업**. Conversation text is reported material; project-journal or canonical-case
promotion still requires independent execution evidence.

Create a knowledge candidate explicitly and publish only after its category-specific evidence
gate, reusable-content quality gate, and the qualified local evidence editor's deterministic
citation checks pass. Codex reports, the extractor, the embedding model, and unqualified
generation output cannot approve a case. Exact
problem/root-cause/resolution duplicates add occurrences and revisions to the existing canonical
case; uncertain similarity remains `NEEDS_REVIEW`.

### GPU semantic duplicate worker

New auto-generated canonical cases first retain deterministic exact-duplicate behavior: the same
problem/cause/solution adds an occurrence and revision without a model call. A non-exact candidate
then remains `pending_gpu_vector_check`. `knowledge_dedup` extracts Korean/English Unicode key
terms, uses them to bound a pgvector cosine lookup of rebuildable candidate/case vectors, and marks
near matches `NEEDS_REVIEW`; it does not merge ambiguous cases automatically. No candidate is
auto-published before this check completes.

The job must never use the CPU Ollama service. Production runs it as the first stage of the nightly
maintenance reservation; inspect the pending count and nightly task with:

```powershell
Get-ScheduledTask -TaskPath '\LocalKnowledgePortal\' -TaskName 'NightlyKnowledgeMaintenance'
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/dedup/status
```

The semantic stage shares one embedder with retrieval verification, is queued behind earlier GPUQ
reservations, and has no direct-GPU fallback.

### Canonical case search projection

Publishing a verified candidate writes a deterministic UTF-8 page under
`_generated/Knowledge-Cases`, records it in `generated_page`, and enqueues a priority indexing
job. The PostgreSQL case and append-only revisions are canonical; the Markdown and vectors are
rebuildable projections.

If guidance changes, create a new evidence-gated candidate with
`metadata.supersedes_case_id=<existing case UUID>`. Publication appends a revision and occurrence
before updating the current page. Never edit a generated page to change the canonical case.

Verify the projection with:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/v1/knowledge/cases
docker compose -f C:\Docker\local-knowledge-portal\compose.yaml exec -T postgres `
  psql -U lkp -d lkp -c "select relative_path, pipeline_version from generated_page"
```

Legacy `_generated/Runbooks` pages are ignored, not deleted. If a canonical page is missing, call
`POST /api/v1/knowledge/cases/{id}/materialize`; the operation refuses cases without verified
evidence.

Audit generic auto-published cases with a dry run before applying reversible retirement:

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f /mnt/c/Docker/local-knowledge-portal/compose.yaml run --rm --no-deps \
  hook-collector python -m lkp_indexer.knowledge_quality
```

Add `--apply` only after reviewing the exact IDs. Apply changes status to `retired`, returns
candidates to `NEEDS_REVIEW`, and moves only portal-managed pages to
`_generated/_retired/Knowledge-Cases`. It preserves cases, revisions, evidence, original extracted
fields, and files; the retired directory is excluded from indexing.

Start a new Codex session, run `/hooks`, inspect the six commands, and approve them. Codex owns
this trust boundary, so the portal records `MANUAL_APPROVAL_REQUIRED` until the operator acts.

### Local evidence editor

Historical candidate configuration used `gemma4:e4b`; it is superseded by the current
GPU-queued editor subsection below. One Windows Ollama daemon owns
`E:\AI\Models\Ollama\generation\models` and serves Hermes, portal embedding, and portal knowledge
editing. Its listener remains loopback-only; portal containers use
`http://host.docker.internal:11434`. A generation model remains
unloaded until the scheduler observes at least 12,288 MB free VRAM, at most 15% utilization, and
at most 70°C. Busy checks back off from 15 minutes to 4 hours; after six checks the scheduler
waits 24 hours. A scheduled run processes every eligible candidate present at its start; candidates
arriving during that run wait for the next schedule. The editor is sequential and the model unloads
after two minutes.

Inspect status without triggering inference:

```bash
curl -s http://127.0.0.1:8010/api/v1/knowledge/curation/status
powershell.exe -NoProfile -Command "$env:OLLAMA_MODELS='E:\AI\Models\Ollama\generation\models'; ollama list; ollama ps"
curl -s http://127.0.0.1:8010/api/v1/gpu-queue/status
```

This historical candidate was never authorized merely by being available. Every replacement still
requires an exact digest and prompt/harness qualification result of `PASS`. Generated prose never
satisfies an evidence gate without independent execution evidence.

The Compose services `ollama`, `ollama-generation`, and `ollama-embedding-batch` are rollback-only
behind the disabled `legacy-ollama` profile. Do not start them during normal operation. A rollback
must stop Windows Ollama first and preserve both model stores; never delete the shared model root.

### Evidence-bound developer feed

`developer-feed` is a one-shot GPU-queued editor, not an always-on service. The production
schedule invokes it once inside the 07:30 daily pipeline. It writes newly embedded information,
summarizes the previous local calendar day, and unloads `gemma4:12b` in `finally`.

The editor receives redacted verified journal facts plus excerpts from the current embedded document
versions. Its prompt explicitly prohibits embedding/index/model-operation narration: posts must
describe the actual information that changed, what was learned, and supported results. Each post
cites exact source IDs in metadata and a deterministic validator rejects invented IDs. One repair
is allowed for schema, citation, or length errors; a second failure publishes nothing.

Each update is stored as one four-part bilingual X-style narrative: observed change, practical
meaning, a clearly future-tense possibility, and a casual afterthought from the workstation
developer. The persona sounds like a developer sharing recent tinkering with a small circle, not a
manifesto or personal philosophy. Korean is drafted first as an informal firsthand work note,
uses ordinary `해요/했어요/됐네요` cadence instead of report-style `합니다/습니다`, and rejects
generic translated editorial jargon. Vague product-language padding such as “가독성”, “소통
도구”, or “확장 가능성” also fails validation; the reply must name a concrete action, awkward
moment, or next experiment. English is independently localized from the same facts instead of
being translated sentence by sentence. The closing afterthought may describe a small reaction or
annoyance from that work session, but must not introduce a principle, duty, future policy, or
self-imposed rule.
Every reply combines two or three sentences and carries enough context to avoid release-note
fragments. Possibilities are recorded as `claim_mode=proposal` and afterthoughts as
`claim_mode=personal_aside`; neither is promoted as verified fact. Korean posts are limited to 140
Unicode code points and English posts to 280. The UI defaults to Korean.
The source manifest records journal/document/version provenance, the embedding revision/time, and
the exact Gemma model digest and prompt version. A screenshot is only recommended when a source
explicitly identifies a stable non-secret visual artifact; capture remains a separate reviewed
action.

At 07:30 `Asia/Seoul`, the daily entrypoint creates the idempotent `daily:YYYY-MM-DD`
synthesis for the previous local calendar day. A day with no verified update gets a deterministic
transparent closeout without loading the model.

```powershell
.\scripts\install-nightly-maintenance-schedule.ps1
.\scripts\nightly-maintenance.ps1
Invoke-RestMethod http://127.0.0.1:8010/api/v1/developer-feed/status
Invoke-RestMethod 'http://127.0.0.1:8010/api/v1/developer-feed?language=ko'
```

Rollback is application-safe: disable `\LocalKnowledgePortal\PublishInformationFeed`, redeploy the
prior API/web image, and restore the prior schedule only if the old persistent service is desired.
Feed rows are derived, non-authoritative records. Do not downgrade the database during an ordinary
rollback.

### Nightly GPU maintenance

The workstation registers only `\LocalKnowledgePortal\NightlyKnowledgeMaintenance` for automatic
model work. It starts daily at 07:30, submits one 14,336 MiB GPUQ reservation, and waits for that
reservation even when earlier interactive work delays admission. Task Scheduler uses `IgnoreNew`
and a 48-hour bound, so a delayed run is not duplicated at the next trigger.

The admitted wrapper runs four checkpointed stages in order:

1. after a bounded ingest-queue quiet check, deferred document reindexing, semantic duplicate
   checks, and semantic/hybrid retrieval verification share one `qwen3-embedding:0.6b` instance;
2. knowledge curation and project-article refresh share one `qwen3.5:9b-q4_K_M` provider;
3. documents written during generation receive one incremental embedding refresh;
4. only after that checkpoint, the previous day's information feed uses `gemma4:12b`.

Stage failures are logged and do not hide the remaining independent stages; the GPUQ job exits
non-zero if any stage failed. Stable one-shot container names plus an exit trap remove only this
pipeline's abandoned containers. Each embedder/editor closes its exact model before exit. The
installer disables `CurateKnowledge`, `DeduplicateKnowledge`, `PublishInformationFeed`,
`ValidateSemanticRecovery`, and `ReapGpuEmbeddingBatch` but leaves them registered for rollback.
Feed publication is skipped fail-closed when the post-generation embedding checkpoint is not
ready. `OBSERVED_CHANGE` journals may enter the feed only when a matching current document is
embedded; their claim scope cannot represent success, effects, causes, or metrics as verified.
The feed reuses one bounded Gemma load while draining up to eight 12-source batches. Cursor order
remains ascending so backlog is processed without skipping older embedded evidence.

### Model-specific nightly performance profiles

Do not copy one model's tuning values to another model or runtime. The GPU-only
`qwen3-embedding:0.6b` profile uses `LKP_GPU_EMBEDDING_BATCH_SIZE=4` and
`LKP_GPU_EMBEDDING_BATCH_COOLDOWN_SECONDS=0.05`; the ordinary CPU worker remains at batch one and
one-second cooling. The previous GPU baseline embedded 293 deferred documents in 778 seconds with
batch two and 0.5-second cooling while leaving substantial GPU headroom. Keep the recursive timeout
split and compare the next equivalent run before increasing beyond four.

Both generation models explicitly use llama.cpp/Ollama logical batch 1024, matching the observed
Ollama runner and the workstation's other bounded Ollama workloads. `qwen3.5:9b-q4_K_M` uses a
32,768-token context because the 2026-07-31 nightly run observed prompts up to 16,262 tokens at the
former 16,384 limit, followed by truncated JSON and citation repair failures. The same run measured
41 Qwen decode samples averaging 168.04 tokens/second and prompt processing averaging 9,103
tokens/second. The shared nightly reservation is 14,336 MiB because the former 16K profile already
peaked at about 11.33 GiB; this is admission headroom, not permission to keep the model resident.
`gemma4:12b` keeps a 16,384-token context: its feed prompts were 12,173 and 13,045 tokens and decoded
at 113.36 and 113.86 tokens/second without context truncation.

The portal knowledge search found no reusable throughput profile for these exact model,
quantization, and runtime triples. The local DOM translator does use batch 1024 for
`gemma4:e4b`, and separately uses two-token MTP with Gemma 4 E2B QAT under vLLM. Those are useful
cross-checks, but neither is the portal's Ollama `gemma4:12b` target, so only the independently
observed batch setting is shared; its short translation context, concurrency, and MTP settings are
not transplanted.

Qwen's official 9B model card recommends MTP speculative decoding and Google documents a dedicated
Gemma 4 draft model, but the active Ollama GGUFs expose no MTP/draft tensors and Ollama cannot attach
the separate cached Gemma assistant artifact through the current API contract. Do not set
`draft_num_predict` as a cosmetic flag. A future MTP change requires a separate runtime-qualified
profile, digest-pinned target and assistant, quality comparison, VRAM measurement, and gpuq cleanup.
Reference:

- <https://huggingface.co/Qwen/Qwen3.5-9B>
- <https://ai.google.dev/gemma/docs/core>
- <https://arxiv.org/abs/2607.02770>
- <https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md>

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\install-nightly-maintenance-schedule.ps1 -Hour 7 -Minute 30
Get-ScheduledTask -TaskPath '\LocalKnowledgePortal\' |
  Where-Object TaskName -Match 'Nightly|Curate|Deduplicate|Feed|Semantic|Embedding'
```

### Current GPU-queued evidence editor

The qualified production editor is `qwen3.5:9b-q4_K_M`, digest
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`. The
smaller candidate was not forced into service after it failed to demonstrate reliable structured
evidence editing.

The persistent curator is disabled from the default Compose profile. Submit one bounded batch
through the workstation GPU reservation authority:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  \\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\curate.ps1
```

`curate.ps1` checks for an existing `local-knowledge-portal-curation` workload, then calls
`gpuq run --vram 8192 --eta 1800 --max-runtime 21600 --priority 40`. It does not run the model
directly or queue a duplicate workload while an equivalent job is active or waiting. The command
starts a one-shot
`knowledge-curator` container with the `manual-curation` profile. Host scheduling owns GPU
admission, fairness, safety VRAM, bounded waiting, and job logs.

The deduplication check explicitly collects matching active **and** queued rows
into an array before counting. If it logs `Curation is already queued or active`,
the task must exit successfully without submission. This prevents a repeating
hourly task from accumulating duplicate snapshots while the GPU scheduler has
another admitted workload.

The curator freezes eligible candidate IDs immediately after acquiring its PostgreSQL advisory
lock. It attempts the complete frozen set, records processed/unchanged/failed counts, and isolates
each candidate with a savepoint so one malformed model response cannot block later candidates.
Candidates created or updated after the cutoff are intentionally handled by the next run.
Journal-backed candidates previously held in `needs_review` are retried once per curator harness
revision. A failed deterministic claim/citation validation is sent through exactly one constrained
repair request; a second failure remains fail-closed in `needs_review`. The harness revision marker
prevents repeated model loads for unchanged failed candidates.
The external one-shot schedule is the retry clock, so each invocation probes the GPU again even
when the previous run recorded an internal backoff timestamp. Only the optional persistent loop
honors `next_attempt_at` between its own polls.

Register the daily, non-overlapping maintenance task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\scripts\install-nightly-maintenance-schedule.ps1 -Hour 7 -Minute 30
```

The installer copies the stable nightly entrypoint and queue helper to
`C:\Docker\local-knowledge-portal`, backs up a differing prior copy, and registers
`\LocalKnowledgePortal\NightlyKnowledgeMaintenance`. It does not store a scheduler token.

Each run asks the API for both eligible knowledge cases and due project
articles. It does not reserve the GPU when both counts are zero. A project
article is due when it is missing, a previous edit failed or was interrupted,
the configured model/prompt/digest changed, or a current document/journal
version is newer than the last comparison. The worker processes every
registered `project_key` fairly within
`LKP_PROJECT_ARTICLE_MAX_PROJECTS_PER_RUN`; missing projects cannot be starved
by previously current projects. New repositories need no scheduler or code
change after their source root is registered and indexed.

Inspect coverage without starting a model:

```powershell
(Invoke-RestMethod `
  http://127.0.0.1:8010/api/v1/knowledge/curation/status).project_articles
```

`current + due` must equal `projects`. `processing` after a host interruption
is intentionally retryable. The project page shows a pending state instead of
presenting raw activity extracts as a completed article.

The exact digest passed prompt `evidence-blog-v9` with deterministic harness
`evidence-gate-v3`. Reported-only success and unmeasured performance claims remained unpublished.
The model only edits verified inputs. Deterministic code owns candidate state, publication,
evidence binding, duplicate occurrence, revision, project/tag taxonomy, generated page paths, and
project overview refresh. The model's `decision` is advisory. There is no minimum article length;
optional sections are omitted instead of padded.

Inspect the scheduler without triggering inference:

```bash
curl -s http://127.0.0.1:8010/api/v1/gpu-queue/health
curl -s http://127.0.0.1:8010/api/v1/gpu-queue/status
```

The portal's `/gpu-queue` UI reads host capacity and observed external GPU processes. It can also
request a graceful stop for a scheduler-managed job and persist a full queued order; it never
stops an untracked process. The browser calls only the portal API. The host mutation token is held
only by the API container after `scripts/configure-gpu-queue-control.ps1` copies it from the local
scheduler configuration into the ignored portal environment file.

Queue reorder is optimistic-concurrency protected: the browser must submit every current queued
UUID exactly once. A changed queue returns conflict rather than reordering different work. An
explicit order remains subordinate to the host VRAM safety reserve, parallel-job limit, and safe
short-job backfill when the requested head cannot fit.

If the driver observes GPU use but no scheduler-managed job, `/gpu-queue` displays it as external
usage. It does not invent a queue job or offer a stop button for that process. Windows WDDM may
hide per-process VRAM, so the dashboard separates device-wide usage from process identity. A
pre-warmed ComfyUI server is one such external consumer: its `/prompt` bridge still fails closed
through `gpuq`, but memory held before a prompt is not retroactively a scheduler reservation.

## Queue recovery

An interrupted processing job becomes claimable after `lease_expires_at`. Failed jobs back off exponentially and become `dead_letter` after `max_attempts`. The UI retry action creates a new job whose `error_details.retry_of` points to the original.

One source file is one durable job. Do not combine unrelated files merely to reduce the visible
pending count: file-level jobs preserve idempotency and provenance. The portal distinguishes active
backlog from cumulative completed history and estimates drain time from the preceding three hours.
Ready and expired-lease partial indexes keep claims bounded as history grows.

Before embedding, the worker applies `purpose-aware-v3`. Generated tokenizer payloads are ignored.
Repository code and nested Git dependencies are lexical-only in the default `docs_only` mode.
Lockfiles, minified/generated files, documents above 128 chunks, and documents above 250,000
characters are also indexed lexically with an explicit skip reason. Source-root
`semantic_exclude_patterns` keep generated evidence such as `docs/evidence/**` available to
path/keyword retrieval and provenance while reserving vectors for curated documentation and
project journals. Change the mode or limits
through configuration only after retrieval evaluation; do not remove the guard to make an initial
scan appear faster.

## Watcher incident

Treat file events as hints. If watchfiles fails, stop only the watcher and run reconciliation/scan. Do not touch source files. After Windows suspend/resume, run reconciliation before trusting freshness.

The WSL2 Docker deployment deliberately uses per-root hybrid monitoring:

- WSL ext4 roots such as `/home/kutae/src` use native filesystem notifications.
- Windows bind-mounted roots such as `/data/vault` use bounded polling. Keep
  `LKP_WATCH_POLL_DELAY_MS` at or above 1,000 ms; the default is 2,000 ms.
- Reconciliation remains enabled even when native events appear healthy.

In Operations → Workers, inspect the stable `watcher-service` row. Normal state is `healthy`
with `cpu_alert=false`; its metadata shows `root_watch_modes` and `process_cpu_percent`.
After the startup grace period, three consecutive samples at or above the configured 50%
threshold set the row to `error` and emit `watcher_cpu_alert_changed`.

If the alert fires, inspect source-root inode growth, accidental force-polling settings,
poll delay, and high-churn generated directories. Do not globally force polling for a large
WSL root. Use `scripts/watchfiles-bind-probe.py` to verify event delivery on a test bind mount,
then confirm missed events are recovered by reconciliation.

## Embedding incident

If Ollama is unavailable, keyword retrieval over indexed content remains available. Repeated
`ReadTimeout` events open the durable one-hour embedding circuit after the configured threshold.
New documents still receive a version, chunks, lexical search vectors, and provenance, but their
metadata is `embedding_status: deferred_runtime`; semantic and hybrid queries explicitly return
`*-degraded-keyword-only` without waking Ollama. This is intentional fail-closed behavior, not a
semantic-search success. A dimension mismatch remains a hard failure. Correct the configured
model/dimension or create a new embedding revision and explicitly reindex.

### CPU and thermal guard

The WSL2 Compose deployment applies one CPU to worker and API and half a CPU to watcher, hook
collector, and web. Do not remove these limits to accelerate an initial scan. Semantic throughput
is intentionally bounded
with one-chunk embedding batches, inter-batch and inter-job delays, and a 20-job burst cooldown.
Lexical-only jobs never call Ollama and use a separate 200-job burst with a short cooldown under
the same worker CPU limit.

Windows Ollama is shared with Hermes, so a portal request may load an embedding model on the GPU.
Interactive requests use bounded timeouts and a revision-aware query cache; backfill and curation
must submit through `gpuq`. Keep `LKP_EMBEDDING_RUNTIME_MODE=deferred_gpu_recovery` when ordinary
ingestion must not wake the model. Loaded models are visible in `/gpu-queue` and can be unloaded
through the allowlisted graceful control without terminating Ollama or unrelated GPU processes.

### GPU embedding recovery

Use `scripts/reindex-embeddings.ps1` for documents marked `deferred_runtime`. It submits the
one-shot reindex application through `gpuq`; it is not a direct GPU command. The wrapper
reserves 8 GiB by default because a measured `qwen3-embedding:0.6b` run consumed about 6.2 GiB
above the concurrent baseline. Do not lower that reservation without a new measured run. After admission, the
one-shot container uses the private Compose `postgres` endpoint and the single Windows Ollama
through `host.docker.internal`; it never starts a second Ollama daemon.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\reindex-embeddings.ps1
```

Query the returned job ID with `gpuq status` or `GET http://127.0.0.1:8790/api/jobs/<id>`. If the
reservation is wrong, cancel only the portal-owned job with `gpuq cancel <id>`, correct the
reservation, then submit a new job; never stop another workload's process or container directly.

### CPU semantic maintenance interlock

After a CPU embedding timeout or thermal incident, set
`LKP_EMBEDDING_RUNTIME_MODE=deferred_gpu_recovery` in the ignored operational
environment file and recreate the API and worker. This is an explicit,
restart-safe interlock, not a timer: worker ingestion continues with lexical
chunks and marks eligible documents `deferred_runtime`; semantic and hybrid
query requests return `*-degraded-keyword-only` without waking CPU Ollama.

The temporary `embedding-reindex` service sets
`LKP_EMBEDDING_TIMEOUT_CIRCUIT_BYPASS=true` in its private, gpuq-admitted
environment, so the GPU job can repair deferred vectors while ordinary
containers remain blocked. Do not change the runtime mode back to `enabled`
until that job has completed and an actual semantic plus hybrid query has been
checked with source provenance. A timeout window expiring is not evidence that
the CPU path is safe again.

After the reindex job has succeeded, submit the separate GPU-held retrieval
probe rather than enabling CPU embedding just to test it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  "\\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\validate-semantic-recovery.ps1"
```

It reserves the same measured 8 GiB, starts the temporary batch Ollama only
through `gpuq`, and performs a semantic plus hybrid search from a current
already-indexed chunk. The atomic runtime snapshot records counts, similarity,
and provenance completeness but never query/source text. A failed probe is a
failed recovery; leave the CPU interlock in place and investigate the GPU job
or embedding revision instead of claiming semantic retrieval is restored.

The normal CPU worker stays at `LKP_EMBEDDING_BATCH_SIZE=1`. The independent
GPU-only recovery uses `LKP_GPU_EMBEDDING_BATCH_SIZE=4` with
`LKP_GPU_EMBEDDING_BATCH_COOLDOWN_SECONDS=0.05`; it remains gpuq-bounded and
splits a timeout back to single inputs. This is not a change to watcher polling,
CPU worker concurrency, or GPU scheduler parallelism.

For a complete semantic refresh, submit
`scripts/run-gpu-semantic-maintenance.sh` as the argv of one 8 GiB `gpuq`
reservation. It waits for ingest quiescence, refreshes deferred document chunks,
refreshes every searchable current repository-analysis item, prunes vectors that
belong only to stale/rejected repository snapshots, performs knowledge duplicate
checking, and runs the semantic recovery probe. All stages reuse one embedder.
The embedder records whether the exact model was resident before the task; it
never unloads a pre-existing shared model. A task-owned model is unloaded with
Ollama `keep_alive=0` and `/api/ps` is checked afterward. Cleanup failure makes
the GPU job fail instead of being hidden.

Keep `LKP_QUERY_EMBEDDING_PREWARM=false`. While the query embedding circuit is
open, hybrid search first finds current lexical anchors and averages up to three
of their existing vectors to expand the candidate set. This model-free
`hybrid-seeded-vector` path does not wake Ollama. Candidate generation remains
at least 80 rows even when the caller requests Top-5; stale revision, project
scope, invalid provenance, reported journals, and rejected repository items are
gated before reward-guided Top-K selection.

### Codex MCP retrieval contract

Codex hook ingestion and knowledge retrieval are separate. Hooks append local
activity for later indexing; they do not inject evidence into a task. The
read-only `local_knowledge` MCP exposes `retrieve_context`, `verify_answer`, and
`get_source`. Retrieval automatically routes architecture/lifecycle questions
to the current repository-analysis snapshot, failure questions to verified
cases plus repository evidence when available, and ordinary questions to
current document chunks. Full clauses are preserved and at most two bounded
subqueries are issued.

Only high-confidence `source` or `verified` contexts may support an answer.
Reported journals and derived summaries are navigation only. A factual answer
must submit explicit claim/evidence-ID pairs to `verify_answer`; the server
selects the strongest verified candidate, allows one repair after rejection,
and returns strict no-answer after a second rejection. Exact repository
references are checked against the selected snapshot path, source hash, and line
bounds before they leave the API.

The former ten-minute `\LocalKnowledgePortal\ValidateSemanticRecovery` task is disabled. Retrieval
verification now follows duplicate checking inside the shared nightly embedding stage and writes
the same atomic validation snapshot.

`ReapGpuEmbeddingBatch` is retired because embedding jobs no longer start a temporary Ollama
container. Existing installations remain disabled for rollback history. The installer is now
idempotent retirement logic and cannot recreate or start the old monitor.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\install-gpu-embedding-reaper.ps1
Get-ScheduledTask -TaskPath '\LocalKnowledgePortal\' -TaskName 'ReapGpuEmbeddingBatch'
```

Inspect the effective cgroup limits and current load:

```powershell
docker inspect -f '{{.Name}} NanoCpus={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}' `
  local-knowledge-portal-worker-1 local-knowledge-portal-api-1
docker stats --no-stream local-knowledge-portal-worker-1 `
  local-knowledge-portal-api-1 local-knowledge-portal-watcher-1
```

If temperature or total CPU remains unsafe, stop only model ingestion:

```powershell
docker stop --timeout 20 local-knowledge-portal-worker-1 `
  local-knowledge-portal-ollama-1
```

API, PostgreSQL, web, watcher, lexical search, and raw Codex spooling remain available. To pause
before the next lease without stopping containers, atomically create
`E:\Data\LocalKnowledgePortal\runtime\embedding.pause`. The worker heartbeat changes to `paused`.
Delete only that exact operator-created file to resume.

The expected worker heartbeat metadata includes `resource_guard_enabled=true`, batch and cooldown
values, and `pause_requested`. During a long job, both `last_seen_at` and `lease_expires_at` must
continue advancing. If either stalls, stop the worker and inspect transaction locks before adding
another worker.

The Ollama container mount must resolve to
`E:\AI\Models\Ollama -> /root/.ollama`. PostgreSQL remains in the Docker Desktop named volume on
C:, application data/spool/vault remains on E:, and append-only backups remain on D:. Verify the
effective mount with `docker inspect`; do not infer model placement from the Docker VHDX location.

Detailed evidence:
`docs/evidence/ollama-embedding-cpu-remediation-2026-07-23.md`.

## Low-value derived-data cleanup

Stop the watcher and worker, create and restore-test an immutable logical backup, then inspect the
cleanup dry run:

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f infra/docker/compose.wsl.yaml exec -T api python -m lkp.cleanup_derived
```

Apply only after the dry-run paths are confirmed:

```bash
docker compose --env-file /mnt/c/Docker/local-knowledge-portal/.env \
  -f infra/docker/compose.wsl.yaml exec -T api python -m lkp.cleanup_derived \
  --apply --manifest /data/exports/cleanup-ignored-YYYY-MM-DDTHHMMSSZ.json
```

The command targets only rows already classified `ignored`, refuses to run while target jobs are
active, deletes embeddings/chunks/versions/documents in dependency order, preserves source files,
and detaches rather than deletes ingest-job history. Validation activity cleanup requires the
additional explicit `--include-validation-fixtures` flag and recognizes only allowlisted fixture
session IDs.

## Isolated integration verification

Do not run integration tests against the production database. From WSL, use:

```bash
sh scripts/run-isolated-integration-tests.sh
```

The runner creates a unique `lkp_test_verify_*` database on the private Compose
network, mounts the repository read-only into a one-off test container, and
uses an exit trap to drop only that exact database after the test process
ends. A name collision fails before cleanup is armed; no existing test or
production database is reused or dropped.

## Shutdown

Stop worker loops gracefully so their current transaction rolls back and lease recovery can occur.
Run `./scripts/docker-stack.sh stop`. This stops every application container without deleting the
Docker named volume, E: data directory, or raw spool.

## Windows logon startup

Register the per-user logon task from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "\\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\install-auto-start.ps1"
```

The task `\LocalKnowledgePortal\StartAtLogon` starts Docker Desktop when necessary, invokes
Compose from the Ubuntu WSL distribution so Linux and `/mnt/*` bind paths retain their intended
meaning, and waits for API readiness. It does not build images during logon. Structured results
are appended to `E:\Data\LocalKnowledgePortal\runtime\logs\startup.jsonl`.

Do not replace the WSL Compose invocation with Windows `docker compose` while the repository and
environment use Linux paths. Doing so can create syntactically valid containers with empty bind
mounts. Validate the task after changes:

```powershell
Start-ScheduledTask -TaskPath '\LocalKnowledgePortal\' -TaskName 'StartAtLogon'
Get-ScheduledTaskInfo -TaskPath '\LocalKnowledgePortal\' -TaskName 'StartAtLogon'
docker compose -f C:\Docker\local-knowledge-portal\compose.yaml ps
```

`LastTaskResult` must be `0`, API readiness must return HTTP 200, and watcher, worker, and hook
collector must remain running.

## Codex activity capture boundary

Raw global hooks only write bounded atomic envelopes to
`E:\Data\LocalKnowledgePortal\ingest\codex-spool`. The collector discards lifecycle events,
acknowledgements, screenshots, file reads, status checks, and other low-signal tool output after
claiming it. It promotes file mutations, failed commands, and explicit test/build/backup/restore
operations into activity history.

The collector reads only `C:\Users\kutae\.codex\sessions` through a read-only container mount to
resolve the tool call's recorded exit code and, when needed, the current turn's user instruction
from a bounded transcript tail. It does not mount the rest of `.codex`.

Activities are not documents and are never chunked or embedded. A reported assistant result
remains narrative, not evidence. Across all Codex sessions, a completed turn becomes an automatic
candidate only when the same turn contains both a successful meaningful file mutation and a
successful test/lint/validation/build command with observed exit codes. Incomplete or partial
reports stop at the review-candidate state. Only a candidate that passes the existing evidence
gate may be published and projected into `_generated/Knowledge-Cases`.

When auditing capture, compare these layers separately in the portal:

- **Activity**: selected global work history, including reported and verified result columns.
- **Candidate review**: evidence-backed but incomplete or ambiguous work.
- **Knowledge cases**: verified canonical records only.
- **Documents**: source files and managed case projections; activity rows do not inflate this
  count.

If a reported success has no observed command exit, leave it `UNVERIFIED`. Do not manually edit
the managed Markdown page to work around the gate; attach evidence to a new candidate or rerun the
validation.

## Data lifecycle and rolling policy

Rolling is tiered and non-destructive by default:

- Raw hook envelopes are removed only after an idempotent DB import; malformed/unclaimed envelopes
  remain for diagnosis or retry.
- After 30 days, only successful unlinked `PostToolUse` detail is logically rolled up. Prompts,
  stop results, failures, and evidence remain visible.
- After 90 days, only succeeded/cancelled queue jobs and routine `indexed`/`ignored`/`unsupported`
  events are marked `rolled_up` and omitted from default Operations queries. Dead-letter, failed,
  renamed, deleted, and restored evidence remain hot indefinitely.
- Source documents, versions, verified cases, revisions, backups, and all embeddings remain
  authoritative or rebuildable according to their existing policies; none are automatically deleted.

The retention pass records its counters in the hook-collector heartbeat. Change
`LKP_ACTIVITY_DETAIL_RETENTION_DAYS`, `LKP_TERMINAL_JOB_DETAIL_RETENTION_DAYS`, or
`LKP_INGEST_EVENT_DETAIL_RETENTION_DAYS` only after a capacity review. Physical purge is not an
automatic feature: it requires an explicit backup, inventory, and operator authorization.

## Project knowledge registration

A project appears in **프로젝트 지식** when at least one of these durable
sources exists:

- an active Markdown/text document indexed with that project key; or
- a completed Codex turn with a meaningful file change attributed to a
  canonical repository under `C:\Dev\Repos` or `/home/<user>/src`.

Attribution uses the repository found in changed-file paths first, then the
first canonical repository observed for the Codex session. A temporary current
directory or nested tool folder must not create a separate project. Changes
without a canonical repository scope remain activity history and are marked
`OUT_OF_PROJECT_SCOPE`; they are excluded from the project list and article
editor without deleting their provenance.

A successful mutation can create an `OBSERVED_CHANGE` development-journal
entry. It becomes `VERIFIED` only when a test, build, or validation command
with an observed successful exit is present in the same completed turn.
Reusable knowledge-case publication retains its stricter evidence gate.
In-progress turns are not folded into the canonical project article until a
`Stop` event closes the turn; this prevents partial work from being presented
as a completed project update.

## Logs

Runtime files belong under `E:\Data\LocalKnowledgePortal\runtime`; container logs are available
through `./scripts/docker-stack.sh logs [service]`. Structured application logs never include full
source content.

## Workstation service management

Install or repair the loopback-only host manager from Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "\\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\install-service-manager.ps1"
```

After changing only the host-manager source, deploy it without rebuilding the
portal containers or rereading the service registry:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "\\wsl.localhost\Ubuntu\home\kutae\src\local-knowledge-portal\scripts\deploy-host-service-manager.ps1"
```

The deployer creates a timestamped operational backup, places the maintenance
marker so the guard does not race the restart, targets only the exact manager
task, verifies loopback health, and restores the backup automatically on
failure.

The installer preserves an existing registry unless `-RefreshRegistry` is
explicitly supplied, backs up the operational `.env` before adding a secret,
registers `\Codex\Local Knowledge Service Manager` for logon startup, and
registers the CPU-only `\Codex\Local Knowledge Service Manager Guard` for
logon plus a five-minute exact-task health check. The guard is independent of
GPU, embedding, generation, and nightly LLM schedules. Never disable either
control-plane task when pausing those workloads. The
registry is
`C:\Docker\local-knowledge-portal\config\managed-services.json`; runtime logs
are under `E:\Data\LocalKnowledgePortal\runtime\logs`. Never put the bearer
token in a browser setting, Git file, report, or command transcript.

Verify the manager and portal proxy without displaying the secret:

```powershell
Invoke-RestMethod http://127.0.0.1:8791/api/health
Invoke-RestMethod http://127.0.0.1:8010/api/v1/service-manager
Get-ScheduledTask -TaskPath '\Codex\' -TaskName 'Local Knowledge Service Manager'
Get-ScheduledTask -TaskPath '\Codex\' -TaskName 'Local Knowledge Service Manager Guard'
```

For a deliberate host-manager maintenance window only, create
`C:\Docker\local-knowledge-portal\host-manager\maintenance.disabled` before
stopping or disabling the manager task. Remove that exact marker and run
`scripts\ensure-service-manager.ps1` to resume. Without the marker, the guard
repairs an accidental disable and restores loopback health within five minutes.

The portal's **레포·서비스 관리** page groups registered projects, AI tools, and
shared data infrastructure. Start and stop always use a server-issued,
90-second, one-time confirmation challenge. Opening the dialog only prepares
that challenge; it does not send a control request. **취소** has the initial
keyboard focus and must leave the service state unchanged. A protected service
shows **조회 전용**.

When a registered repository or tool has a browser UI, add an explicit
credential-free loopback `web_url` such as `http://127.0.0.1:8080/` to its
allowlisted registry entry. A healthy service then shows **웹 열기**. Do not
infer web links from published ports: database and internal API ports must not
be presented as user interfaces. URLs with external hosts, credentials, query
strings, or fragments fail closed during registry loading.

ComfyUI's HTTP server is a lightweight `http_process`, not a long-lived GPU
reservation. Starting the UI must not create a `comfyui-server` GPUQ job.
The bridge protects both ComfyUI submission aliases, `/prompt` and
`/api/prompt`, in `prompt_reservation` mode and submits each actual
image-generation request through GPUQ with its configured VRAM and priority.
The obsolete `COMFYUI_GPUQ_SERVER_MANAGED=1` mode fails closed with HTTP 503;
it must never silently bypass per-prompt admission.
This separation keeps an idle UI from blocking repository curation,
embeddings, or other bounded GPU work.

The safe-stop helper refuses to act unless the ComfyUI queue is empty and the
loopback listener belongs to the exact registered `main.py` command. ComfyUI
safe-stop returns HTTP 409 while a generation is running or queued; finish or
cancel that workload in the GPU queue rather than bypassing the guard. Never
replace this with a broad process-name kill.

Verify the boundary after a start:

```powershell
Invoke-RestMethod http://127.0.0.1:8188/gpuq_bridge/health |
  Select-Object ready,reservation_mode,requested_vram_mb,protected_prompt_paths,
    intercepted_prompt_count,submitted_prompt_count,last_gpuq_job_id
Invoke-RestMethod http://127.0.0.1:8790/api/status
```

The bridge must report `ready=true` and
`reservation_mode=prompt_reservation`, and `protected_prompt_paths` must contain
both aliases. A successful user submission must increment
`submitted_prompt_count` and set `last_gpuq_job_id`; `ready=true` alone is not
proof that admission works. The scheduler must contain no active or queued
`comfyui-server` job while the UI is idle.

Keep `LKP_SERVICE_MANAGER_TIMEOUT_SECONDS` short for status probes and
`LKP_SERVICE_MANAGER_ACTION_TIMEOUT_SECONDS` longer than the largest registered
graceful-stop timeout. A 504 does not prove the host action failed: inspect the
service state before retrying, so an already completed stop is not duplicated.

The retired periodic GPU embedding reaper is excluded from aggregate health by
default. Set `LKP_GPU_EMBEDDING_REAPER_HEALTH_ENABLED=true` only when that
scheduled guard is intentionally restored and producing fresh snapshots.

To register a future service, review its canonical path, owner, health
endpoint, start/stop boundary, data dependencies, and safe-stop behavior.
Then update the operational registry through a requested configuration change.
Do not enable `-RefreshRegistry` merely to make every discovered container
controllable.
