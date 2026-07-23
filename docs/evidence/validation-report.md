# Validation report

검증 시각: 2026-07-23 (Asia/Seoul)

저장소: WSL ext4 `/home/kutae/src/local-knowledge-portal`

최종 판정: **VERIFIED**

장시간 watcher endurance, 장시간 부하 유지, 실제 Windows 장시간 절전/복귀만 제외했다.
Codex `/hooks` trust는 제품 보안 경계 때문에 **MANUAL_APPROVAL_REQUIRED**이며, 사용자가
허용한 유일한 수동 승인 항목이다. 애플리케이션은 단일 Docker Desktop WSL2 backend의
Linux 컨테이너로 실행하고 localhost에만 공개한다.

## 구현 결과

- PostgreSQL 18.4 + pgvector 0.8.2, schema `0004_project_semantic_scope`.
- Linux-native source repository는 WSL ext4, 운영 데이터와 managed Vault는
  `E:\Data\LocalKnowledgePortal`, Ollama model은 `E:\AI\Models\Ollama`, Codex raw spool은
  `E:\LocalKnowledgePortal`, immutable backup은 `D:\LocalBackup\LocalKnowledgePortal`에
  둔다.
- scanner → PostgreSQL queue → leased worker → append-only version → symbol/heading chunk
  → Ollama embedding → keyword/semantic/hybrid/RAG API 경로가 동작한다.
- watcher create/modify/delete/rename, 저장 안정화, debounce, reconciliation, lease renewal,
  bounded retry/dead-letter, graceful service scripts, 주기 heartbeat를 구현했다.
- Activity, Knowledge Case, Candidate Review, Explorer, Document Version/Diff, Search,
  Operations, Worker, Backup 화면과 provenance를 구현했다.
- source root에 `data_scope=production|validation`을 추가했다. API 검색/트리/메트릭은
  validation source를 제외하고, 통합·retrieval 테스트는 전용
  `local_knowledge_portal_test` DB만 사용한다. 기존 validation row는 삭제하지 않았다.

## Codex global hooks

설치 파일은 `%USERPROFILE%\.codex\hooks.json`이며 SHA-256은
`78C09A270A463AA2E003C720CD8E0EA0210BEBA19874804473F5020208CF12A8`이다.

- 설치 이벤트: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`,
  `SubagentStart`, `SubagentStop`.
- installer를 반복 실행해 동일 hash와 이벤트별 단일 managed entry를 확인했다.
- 기존 `AGENTS.md`, `config.toml`, `hooks.json`은
  `D:\Backups\LocalKnowledgePortal\config\codex\2026-07-23T181220036`에 백업했다.
- hook은 API/DB를 호출하지 않고
  `E:\LocalKnowledgePortal\ingest\codex-spool\pending`에 temp + fsync + atomic rename으로
  기록한다. fallback은 `%LOCALAPPDATA%\LocalKnowledgePortal\spool-fallback`이다.
- 1 MiB 제한, truncation marker, secret redaction, deterministic event ID,
  malformed/unsupported/oversized quarantine, stale claim recovery를 구현했다.
- 실제 수집: SessionStart 1, UserPromptSubmit 2, PostToolUse 2, Stop 2,
  SubagentStart 1, SubagentStop 1.
- DB를 중지한 세션 `db-outage-61caac8c03e54c4abb93615ecd95a41e`의 raw event가 E:에
  남고, DB 재기동 뒤 동일 instruction으로 수집됨을 확인했다.
- 일반 작업은 `activity_event`로만 저장한다. 이전 transcript→wiki polling 프로세스는
  종료했고 start script에서 제거했다.
- `E:\LocalKnowledgePortal\runtime\hook-trust-status.json`:
  `MANUAL_APPROVAL_REQUIRED`. 새 Codex session에서 `/hooks` 검토/승인만 남았다.

## Activity와 evidence

Activity에는 사용자 지시, tool/command, 변경 파일, exit code, test/build/lint 증거,
document/version 연결, reported result와 verified result를 분리해 저장한다.
`PostToolUse(exit_code=0)`가 뒤늦게 수집되어도 관련 `Stop` activity를 VERIFIED로
보강한다. 실행 증거 없는 성공 보고는 `UNVERIFIED`다.

검증 시나리오 결과:

| 시나리오 | 결과 |
|---|---|
| 단순 CSS 변경 | activity only, canonical case 0 |
| 실패→원인 수정→성공 | verified error-resolution case |
| 동일 오류 재수행 | `MERGED_OCCURRENCE`, 새 case 없음 |
| 같은 증상·다른 원인 | 별도 case + relation 1 |
| 불확실한 유사 사례 | `NEEDS_REVIEW` |
| 측정 없는 성능 주장 | `NEEDS_EVIDENCE`, publish 거부 |
| 원인 + before/after metric | verified performance case |
| 실행 증거 없는 Codex 성공 보고 | `UNVERIFIED` |
| portal/DB 중단 중 작업 | raw spool 후 복구 수집 |

category별 evidence gate는 error resolution, implementation, custom success, performance,
operations를 구분한다. 최종 canonical case 6, exact duplicate 0, occurrence 7, relation 1이다.
검증 스크립트를 다시 실행했을 때 기존 사례는 모두 `MERGED_OCCURRENCE`였고 canonical
case 수는 6으로 유지됐다.

## Ollama와 retrieval

- Ollama Docker stable image: `0.32.1`.
- installer SHA-256:
  `2f53afab45547896e66b2879174ee78bb1f079f4a20b0858e0e377da0c3631f0`.
- model: `qwen3-embedding:0.6b`, 639,150,858 bytes, Q8_0.
- digest:
  `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`.
- 실제 `/api/embed` dimension: 1024.
- production revision:
  `ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`.
- deterministic validation vector revision과 production revision은 분리했다.
- legacy wiki watcher 종료 후 pending 최신 문서를 재색인했다. 확인 시 production
  current chunk/vector는 `45,524 / 45,524`였다.
- dimension/digest mismatch는 fail closed이며 모델 변경은 새 revision을 요구한다.

전용 4문서 Korean/English/code corpus의 baseline:

| 모드 | Hit@5 | Hit@10 | MRR | filter | citation |
|---|---:|---:|---:|---:|---:|
| keyword | 0.6667 | 0.6667 | 0.6667 | 1.00 | 1.00 |
| semantic | 1.00 | 1.00 | 0.8519 | 1.00 | 1.00 |
| hybrid | 1.00 | 1.00 | 0.9259 | 1.00 | 1.00 |

no-answer correctness와 stale document exclusion도 통과했다. 모든 결과는 document,
version, chunk, source root, canonical/relative path, line range, content hash, indexed time,
score와 match reason을 반환한다.

## 복구와 watcher 검증

- create→modify→rename→delete를 실제 managed fixture로 수행했다. rename은 같은
  document ID를 유지하고 rename event를 남겼으며 delete 후 state가 deleted가 됐다.
- watcher를 중지하고 파일을 만든 뒤 one-shot reconciliation으로 누락 문서를 active
  상태까지 복구했다.
- DB 중단 중 생성한 파일도 DB 재기동 후 reconciliation으로 수집했다.
- worker를 job 처리 중 강제 종료했다. job
  `4407ae06-8aee-4295-9e58-e7a55b2c8a6f`은 lease 만료 후 다른 worker가 attempt 2로
  재처리하여 `succeeded`가 됐다.
- PostgreSQL container를 실제 stop/start한 후 collector, worker, readiness가 복구됐다.
- 이벤트가 없을 때 stale로 보이던 watcher heartbeat 결함을 독립 주기 heartbeat로
  수정했다. 재시작 후 watcher/reconciler 4개 row 모두 `healthy` 갱신을 확인했다.

## 실제 실행 명령과 결과

```text
docker compose --env-file <operational-env> -f infra/docker/compose.wsl.yaml run migrate
  PASS: 0003_queue_scale_indexes
bash scripts/validate-container.sh .
  PASS: ruff and 26 unit tests
LKP_TEST_DATABASE_URL=<dedicated DB> bash scripts/test-integration-container.sh .
  PASS: 7 passed, 1 dependency deprecation warning
docker compose --env-file <operational-env> -f infra/docker/compose.wsl.yaml build web api
  PASS: Next.js 16.2.11 production build, TypeScript, API image
docker run --rm --network host mcr.microsoft.com/playwright:v1.61.1-noble ...
  PASS: 4 passed
uv run python tests/retrieval/evaluate.py
  PASS: keyword/semantic/hybrid baseline, no-answer, stale exclusion
bash scripts/backup-wsl-docker.sh
  PASS: D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T151129Z
bash scripts/restore-test-wsl-docker.sh <timestamped-directory>
  PASS: temporary PostgreSQL 18.4 + pgvector restore
```

Playwright는 다음을 실제 확인했다.

1. Overview, Explorer, document version timeline, latest diff, provenance/citation.
2. Activity list/detail과 reported/verified 분리.
3. Knowledge Case list/detail, occurrences/revisions, Candidate evidence gate.
4. keyword/semantic/hybrid 결과.
5. failed/dead-letter job과 retry.
6. worker heartbeat와 backup 상태.

최종 backup manifest:

- dump size: 115,568,192 bytes.
- SHA-256:
  `547fa3e346a716dd78ab9136a68af24a02782061ad280ad3092863cb61d66b64`.
- source configuration hash:
  `9f05c1eff3ee7a658384bc21fbba395e780a607eaf8e5269d78cfb3f8760acb6`.
- restore count: documents 2,312, chunks 20,500, vectors 20,500, activities 1.
- schema `0003_queue_scale_indexes`; 별도 임시 DB 복원과 checksum 검증을 통과했다.
- 설정, managed Vault 2개, raw spool 9개, source/model/pipeline manifest를 같은 immutable
  timestamp에 저장했다. 임시 restore DB는 검증 후 제거했다.

## 검증 중 실패와 교정

- final integration 명령에 과거 example DB password를 사용해 인증 실패했다. secret을
  출력하지 않고 `.env` URL의 database name만 test DB로 바꿔 재실행해 6/6 통과했다.
- Docker build 첫 명령은 compose env file이 없어 interpolation 실패했다.
  `--env-file .env`를 지정해 통과했다.
- Explorer E2E가 5,000-row tree 제한 밖의 multi-version 문서를 선택해 실패했다.
  API/tree data limit를 20,000으로 올리되 project node만 렌더링하고 확장 project당
  250문서만 DOM에 두도록 유지한 뒤 통과했다.
- 이전 Codex wiki poller가 최신 문서를 계속 변경해 261→189 embedding gap을 만들었다.
  poller를 종료하고 activity-only collector만 유지한 뒤 45,524/45,524를 확인했다.
- watcher process가 살아 있어도 무이벤트 구간 heartbeat가 stale였다. 독립 heartbeat
  task를 추가하고 healthy 갱신을 확인했다.
- reindex idempotency key가 varchar(128)을 넘은 초기 실패는 SHA-256 key로 수정했다.

실패를 성공으로 숨기지 않았으며 위 교정 뒤 관련 전체 검증을 다시 실행했다.

## 생략과 남은 수동 승인

합리적으로 제외한 항목은 다음 세 가지뿐이다.

- 장시간 watcher endurance.
- 장시간 지속 부하 endurance.
- 실제 Windows 장시간 절전/복귀.

남은 수동 작업:

1. 새 Codex session에서 `/hooks`를 열고 여섯 Local Knowledge Portal hook을 검토한다.
2. 신뢰할 경우 승인한다.

이 trust 승인은 자동화하지 않았고 현재 상태를 명시적으로
`MANUAL_APPROVAL_REQUIRED`로 유지한다.

## 현재 상태와 rollback

- API `127.0.0.1:8010`: ready, database true, Ollama true.
- Web `127.0.0.1:3010`: HTTP 200.
- worker, watcher, reconciler, hook collector 실행 중.
- 전체 C:\Dev\Repos initial scan은 background queue에서 계속 진행된다. 이는 기능
  미검증이 아니라 실제 운영 source 수집이며 기존 이력과 실패 row를 삭제하지 않았다.

Rollback은 service와 Compose를 중지하고 E:의 derived DB directory를 별도로 보존한 뒤,
검증된 D: custom-format dump를 새 DB에 복원하는 방식이다. source root 파일은 어떤
검증에서도 이동·이름 변경·삭제·수정하지 않았으므로 source rollback은 필요 없다.

**최종 판정: VERIFIED**

## 2026-07-23 WSL2 watcher CPU remediation

- Baseline: watcher CPU remained at 70.82–100.73% with zero watcher events and zero new
  jobs during the preceding 15 minutes.
- Cause: watchfiles 1.1.1 automatically forced polling under the WSL2 kernel and repeatedly
  traversed a 107,426-inode source root.
- Design: per-root hybrid mode (`/home/kutae/src` native, `/data/vault` polling at 2,000 ms),
  300-second reconciliation, polling interval guard, stable CPU telemetry heartbeat, and
  live-event queue priority.
- Verification: ruff passed; 22 unit tests passed; 6 dedicated-DB integration tests passed;
  Next.js production build passed; WSL native bind probe exited 0; E: Vault
  create/modify/rename/delete events were observed.
- Steady state after startup grace: external watcher CPU 0.15–0.71%, internal 0.25–0.27%,
  `healthy`, `cpu_alert=false`.
- Knowledge promotion: candidate `c67e1f0a-58c6-42fc-ba89-3242220a1e9a` passed the evidence
  gate and canonical case `0af5a147-dd2c-4718-80f0-4a9074c3c73a` was created.
- Managed wiki: `/data/vault/_generated/Runbooks/WSL2-Docker-Watcher-CPU.md`; priority-20
  ingest succeeded; keyword and hybrid retrieval returned source provenance.
- Detailed evidence:
  `docs/evidence/watcher-cpu-remediation-2026-07-23.md`.

Verdict for this remediation: **VERIFIED**.

## Post-incident cleanup

- Five jobs created only for the temporary watcher validation paths were retained as audit
  records and transitioned to `cancelled` with `validation_fixture_cleanup` metadata.
- Two stopped historical `watcher-service:*` heartbeat rows were retained and marked
  `retired=true` with the incident cleanup reason.
- Successful one-off `migrate` and `ollama-model` containers were removed; no active volume,
  source file, backup, document, or embedding was removed.
- Cleanup verification: validation-path jobs `cancelled=5`, no matching pending jobs, no
  stopped one-off containers, API readiness true, watcher CPU 0.13%.

## 2026-07-23 Ollama embedding CPU and lease remediation

- User-reported CPU package temperature: 92°C. The available Windows ACPI provider did not expose
  a sensor, so this remains reported rather than verified evidence.
- Verified cause: Ollama measured 1503.60% Docker CPU with no cgroup limit; host CPU was 61.745%.
  The worker submitted unbounded document-level embedding batches with no job/burst cooling while
  2,455 jobs were pending. The watcher was only 0.38%.
- Additional correctness defect: the long processing transaction locked the job and heartbeat
  rows, blocking lease renewal and risking duplicate processing after lease expiry.
- Implemented:
  - Ollama 2-CPU/6-GiB/256-PID hard limit and worker 1-CPU/1-GiB/256-PID hard limit;
  - Ollama concurrency one and bounded internal request queue;
  - two-chunk embedding batches, 0.5-second inter-batch delay, one-second job delay, and
    15-second cooldown after each 20-job burst;
  - operator pause file and worker heartbeat resource-policy metadata;
  - processing/lease transaction separation so heartbeat and lease renewal continue during
    long embeddings.
- Verification:
  - `docker inspect`: Ollama `NanoCpus=2000000000`, worker `NanoCpus=1000000000`;
  - Ollama post-fix samples `80.74–201.85%`, host `18.1–22.5%`, watcher `0.15–0.73%`;
  - a live job's heartbeat and lease expiry both advanced 20 seconds across a 20-second sample;
  - ruff passed, 24 unit tests passed, 6 dedicated-DB integration tests passed;
  - Next.js production build and TypeScript validation passed;
  - temporary test container and named volume removed.
- Knowledge promotion: candidate `60e1b128-33f7-4dbf-a0a2-bac6d341ae16`,
  evidence gate `VERIFIED`, canonical case `2ca980f8-1dfa-40be-b7eb-65756cc6be78`,
  outcome `CREATED_CANONICAL`.
- Managed wiki `_generated/Runbooks/Ollama-Embedding-CPU-Guard.md` was indexed by a priority-20
  job on attempt one: active document, 9 chunks, 9 production embeddings. Hybrid retrieval
  returned keyword/path and semantic reasons with line-level provenance and similarity 0.9616.
- No source file, backup, document history, embedding, or audit row was deleted.
- Detailed evidence:
  `docs/evidence/ollama-embedding-cpu-remediation-2026-07-23.md`.

Verdict for this remediation: **VERIFIED**, excluding long-duration thermal/endurance testing.

## 2026-07-23 Korean localization and dashboard freshness

- The portal now defaults to Korean and exposes an explicit Korean/English selector. The preference
  persists in `localStorage`, and the document `lang` attribute follows the selected language.
- The overview now reports the API snapshot time, last indexed time, latest source modification,
  recently indexed documents, per-source reconciliation freshness, queue age/state, active worker
  states, and the active embedding and pipeline revision.
- The previous static throughput example was removed. The chart is backed by indexed-event counts
  from PostgreSQL over the preceding 12 hours.
- Human-readable explanations distinguish source modification time, detection/index time, and
  reconciliation time. Recent documents link to the document viewer; stale sources are visibly
  labelled rather than silently presented as current.
- The API summary contract was extended without changing the provenance-bearing search and RAG
  contracts used by agents or local LLM adapters.
- Executed validation:
  - `bash scripts/validate-container.sh .`: ruff passed and 24 unit tests passed.
  - Docker Next.js 16.2.11 production build and TypeScript validation passed.
  - Live `/api/v1/metrics/summary` returned measured throughput, eight recent documents, two source
    roots, queue state, worker states, and indexing/source timestamps.
  - In-app browser validation confirmed Korean rendering, English switching, Korean switching,
    persistence after reload, freshness content, recent-document content, and localized provenance.
- A Playwright localization scenario was added. The Windows pnpm runtime could not safely traverse
  the WSL UNC checkout, so the locked workspace was copied into an ephemeral official Playwright
  1.61.1 container and tested against the localhost production services.

Verdict for the localized dashboard: **VERIFIED** by production build, live API data, and interactive
browser checks. The final Playwright CLI run passed all four scenarios.

## 2026-07-23 full localization, knowledge-value filtering, and queue scale correction

### Cause and implementation

- The first localization pass covered the shell and overview only. Explorer, search, activity,
  knowledge cases, operations, timeline, graph, document versions, table headers, statuses,
  empty/error states, and evidence labels still contained English literals.
- The first ingestion policy also treated every supported text payload as semantic knowledge. A
  524,619-byte, 48,895-line generated tokenizer `merges.txt` held one file-level job for more than
  34 minutes. The normal succeeded-job median was 1.16 seconds and p95 was 9.96 seconds.
- All portal screens now follow the selected Korean/English locale. The native language select uses
  the active theme; the verified dark computed style was background `rgb(16, 24, 32)`, foreground
  `rgb(232, 238, 243)`, and `color-scheme: dark`.
- `deterministic-knowledge-value-v1` now separates raw transport, meaningful activity, lexical
  documents, semantic vectors, and evidence-gated canonical cases:
  - lifecycle acknowledgements and read-only tool calls are `filtered_low_signal`;
  - meaningful instructions, changed files, failures, verification/operation commands, and
    reported outcomes become activity rows;
  - generated tokenizer payloads are ignored;
  - lockfiles, minified/generated files, documents over 128 chunks, and documents over 250,000
    characters remain lexical-only with an explicit semantic skip reason.
- One file remains one durable job for idempotency and provenance. Long work is bounded inside the
  file unit. Ready-job, expired-lease, status-history, ingest-event, and hook-status indexes were
  added in non-destructive migration `0003_queue_scale_indexes`.
- The overview now labels completed jobs as cumulative audit history and reports recent three-hour
  completions, measured hourly throughput, and estimated active-backlog drain time.
- Reconciliation now reloads each `source_root` in its own session. This fixed the false two-hour
  stale value while current `reconciled` events were being written.

### Executed verification

- `bash scripts/validate-container.sh .`: ruff passed; 26 unit tests passed.
- Dedicated temporary PostgreSQL 18.4 + pgvector database:
  `LKP_TEST_DATABASE_URL=... bash scripts/test-integration-container.sh .`: 7 integration tests
  passed; clean schema revision was `0003_queue_scale_indexes`. The temporary container used tmpfs
  and was removed after the run.
- Docker Next.js 16.2.11 production build and TypeScript validation passed.
- Live production migration reached `0003_queue_scale_indexes`; all three queue indexes and the
  status-history index were present.
- The known failed tokenizer job was promoted once for validation. It moved from `failed` to
  `succeeded` immediately with an `ignored` event and reason `ignore_rule`; Ollama performed no
  work for that file. Its prior failure and attempt history were retained.
- The first post-deployment reconciliation classified all 20 previously active documents below
  `tokenizer_configs` as `ignored`. Existing document versions, chunks, embeddings, and ingest
  history were retained, while default retrieval now excludes those documents.
- In-app browser checks passed for Korean overview, explorer, search, activity, knowledge cases,
  operations, timeline, graph, English switching, Korean switching, queue rate/ETA content, and
  dark-theme language control.
- Live queue sample after final restart: pending 1,531; processing 1; cumulative succeeded 2,324;
  succeeded in the previous three hours 2,004; measured rate 668.0 jobs/hour; estimated drain 2.29
  hours. Cumulative succeeded rows are history, not active backlog.
- Live resource sample after final restart: API 1.09%, watcher 0.24%, worker 0.41%, hook collector
  0.00%, and Ollama 78.45%. Ollama remained within its 2-CPU quota and the worker within its 1-CPU
  quota.

### Filesystem and model placement

- Repository: WSL ext4 `/home/kutae/src/local-knowledge-portal`.
- Operational configuration: `C:\Docker\local-knowledge-portal`.
- Runtime data/spool/vault: `E:\Data\LocalKnowledgePortal`.
- Ollama model mount verified by `docker inspect`:
  `/mnt/e/AI/Models/Ollama -> /root/.ollama`.
- Installed model verified by `ollama list`: `qwen3-embedding:0.6b`, digest prefix
  `ac6da0dfba84`, 639 MB. Host model files occupied 0.595 GiB under
  `E:\AI\Models\Ollama`; the model is not stored as an application model copy on C:.
- PostgreSQL is the documented exception: Docker named volume
  `local-knowledge-portal_postgres-data` inside Docker Desktop's C: VHDX.
- Append-only backups remain under `D:\LocalBackup\LocalKnowledgePortal`.

The final Playwright CLI run used
`mcr.microsoft.com/playwright:v1.61.1-noble@sha256:5b8f294a...` with the locked pnpm 11.9.0
workspace in an ephemeral container: all four scenarios passed in 8.9 seconds. The suite covered
all-screen localization, persisted language, explorer/version/provenance, activity and knowledge
details, keyword/semantic/hybrid search, retry, heartbeat, and the recorded backup. No
long-duration endurance or thermal soak was claimed.

The final append-only WSL backup
`D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T151129Z` was recorded in `backup_run` and
shown by the Operations API. Its dump was 115,568,192 bytes with SHA-256
`547fa3e346a716dd78ab9136a68af24a02782061ad280ad3092863cb61d66b64`; the manifest also recorded
schema `0003_queue_scale_indexes`, source configuration hash, two managed Vault files, and nine raw
spool files. Restore into a separate temporary PostgreSQL instance passed with 2,312 documents,
20,500 chunks, 20,500 vectors, and one activity. A first restore command incorrectly supplied the
dump file instead of its directory and failed closed; the corrected invocation passed. One earlier
backup attempt (`2026-07-23T151058Z`) failed while preserving DrvFS timestamps, was not registered as
successful, and is explicitly marked `status=failed`; subsequent copies use non-preserving
recursive copy.

Verdict for this correction: **VERIFIED** for full localization, deterministic knowledge-value
selection, migration, live queue behavior, filesystem placement, Playwright E2E, and
backup/restore.

## 2026-07-24 low-value derived-data removal

- Before deletion, watcher and worker were stopped and immutable backup
  `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T151748Z` was created. Restore into a
  separate PostgreSQL 18.4 + pgvector database passed at schema `0003_queue_scale_indexes` with
  2,317 documents, 20,597 chunks, 20,597 vectors, and one activity.
- Dry-run selected exactly 20 `ignored` documents below `tokenizer_configs`, 12 versions, 42
  chunks, and 42 embeddings. The cleanup removed only those rebuildable database rows; source
  files remained readable. Forty-six ingest jobs were retained as audit history with
  `document_id=NULL`.
- Post-cleanup checks returned zero ignored/tokenizer documents and zero orphan versions, chunks,
  or embeddings. Reconciliation completed after watcher restart and did not recreate the ignored
  rows.
- The remaining `wsl-transition-validation` fixture had no evidence references. Its one activity,
  one hook-spool database row, and one raw spool file were deleted. Production now has zero
  activity fixtures and zero raw spool files, while two verified canonical cases and two managed
  incident Runbooks remain.
- Cleanup manifests:
  `E:\Data\LocalKnowledgePortal\exports\cleanup-ignored-2026-07-23T151927Z.json` and
  `cleanup-validation-2026-07-23T152142Z.json`.
- Hook selection was tightened again: general conversation is discarded without a durable row,
  Stop events require a selected work signal in the same turn, and successfully handled raw
  envelopes are deleted rather than retained indefinitely.
- Validation: ruff passed, 26 unit tests passed, and nine dedicated-database integration tests
  passed. Initial runs exposed that the cleanup helper used the global database session and that
  an older Stop-only fixture contradicted the new selection boundary. Dependency injection was
  added, the fixture now includes a selected work instruction, and the complete suite passed 9/9.
  The temporary test containers were removed.
- Live readiness remained healthy. After restart and reconciliation, watcher CPU was 1.01%, worker
  0.09%, and Ollama 157.76% under its two-CPU limit.

Verdict: **VERIFIED**. No source repository file, verified case, managed Runbook, or backup was
deleted. Codex hook trust remains **MANUAL_APPROVAL_REQUIRED**.

## 2026-07-24 purpose-scoped repository retrieval

### Root cause and design correction

- The user-visible values `2,311 documents`, `21,023 chunks`, and `1,509 pending jobs` were not
  2,311 curated knowledge documents. They were a source-file catalog whose code files were labelled
  as documents and sent through the same semantic path.
- `project_key` used the first source-root-relative directory, so all repositories below
  `/home/kutae/src/ai` appeared as one project named `ai`.
- The first correction that selected the nearest `.git` directory exposed a second defect:
  vendored Eigen was incorrectly split from `TRELLIS.2`. The final rule selects the top-level Git
  repository below the source root and keeps nested Git repositories as dependencies.
- Catalog, lexical search, semantic retrieval, and evidence-gated canonical knowledge are now
  separate scopes. The default `LKP_REPOSITORY_EMBEDDING_MODE=docs_only` keeps code available to
  path/keyword/symbol search but embeds only project-owned Markdown and managed Vault content.
  Nested Git dependency documentation is lexical-only.
- One file remains one durable lease/idempotency/version boundary. Semantic and lexical jobs use
  separate scheduling policies: semantic `1 s / 20 jobs / 15 s`, lexical
  `0.05 s / 200 jobs / 2 s`. This fixes throughput without losing per-file recovery.
- Dashboard terminology now says indexed files and search chunks, reports knowledge/code/support
  counts, semantic coverage, and separates initial-scan backlog from live changes. Explorer shows
  repository names and project-relative paths.
- Semantic search defaults to minimum similarity `0.5`; `high` requires `0.6`. RRF position no
  longer makes a low-similarity first result appear high-confidence.

The accepted design is recorded in
`docs/adr/0007-purpose-scoped-retrieval.md`. Migration
`0004_project_semantic_scope` adds `project_relative_path` without deleting existing rows.

### Safe production conversion

- watcher and worker were stopped before conversion.
- Pre-conversion immutable backup:
  `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T154714Z`,
  dump size `119,978,290` bytes. Its separate restore test passed at schema
  `0003_queue_scale_indexes` with 2,316 documents, 21,374 chunks, 21,309 vectors, and zero
  activities.
- First dry-run examined 2,316 active files, found zero missing sources, and selected 20,772
  rebuildable code vectors. Apply removed only those vectors and reclassified projects:
  `TRELLIS.2=1,710`, `pytorch3d=369`, `Step1X-3D=143`, `FlashVSR=92`, managed `_generated=2`.
- A live job then exposed vendored CUTLASS Markdown. The worker was stopped, nested Git dependency
  exclusion was added, and a second dry-run selected 278 dependency vectors. Apply removed only
  those vectors.
- Atomic manifests:
  `/data/exports/purpose-migration-20260723T155100Z.json` and
  `/data/exports/purpose-migration-dependencies-20260723T155700Z.json`.
- Source files, document rows, append-only versions, chunks, jobs, and audit history were not
  deleted. Post-conversion orphan vector count was zero.

Final production state after backlog drain:

| Item | Verified value |
|---|---:|
| active indexed files | 3,817 |
| current chunks | 39,758 |
| current semantic chunks | 269 |
| projects | 6 |
| initial/live pending | 0 / 0 |
| knowledge/code/support files | 70 / 3,458 / 289 |
| current code vectors | 0 |
| current vector extensions | `.md` only |
| skipped nested dependency files | 2,920 |
| skipped repository code/support files | 879 |

All 286 retained historical/current vectors use one production revision:
`ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`, provider Ollama,
model `qwen3-embedding:0.6b`, digest
`ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`,
dimension 1024.

### Load and search verification

- Before the final thermal correction, watcher was `0.14–0.47%` but Ollama reached `191.92%`
  under its former two-CPU quota. This confirms the heat source was embedding, not the watcher.
- Ollama is now hard-capped at one CPU, model parallelism one, embedding batch one, and a
  one-second inter-batch delay. A three-sample active run measured Ollama
  `77.12%, 98.26%, 12.91%`; watcher remained `0.13–0.27%`.
- With dependency Markdown excluded, a live lexical run reduced pending work
  `1,275 → 1,113` in ten seconds. worker measured `10.69–27.12%`, watcher
  `0.19–0.34%`, and Ollama `0–3.28%`. The log recorded a 200-job lexical burst and two-second
  cooldown.
- Final idle sample: watcher `0.91%`, worker `0.23%`, Ollama `0.00%`, API `0.15%`.
- Code keyword search for `flash_attn` returned Python/shell symbol chunks with source path,
  version, hash, and line provenance while code had no vector.
- Weak semantic query `PostgreSQL queue recovery` returned `confidence=none`, zero results.
  Incident query `Ollama embedding CPU overheat guard` returned the managed Runbook with
  similarity `0.9076` and `confidence=high`.

Long-duration thermal/endurance testing remains explicitly excluded; no package-temperature sensor
was available to verify the user's reported 92°C directly.

### Final executed verification

```text
bash scripts/validate-container.sh .
  PASS: ruff, 30 unit tests
LKP_TEST_DATABASE_URL=<ephemeral tmpfs PostgreSQL 18> \
  bash scripts/test-integration-container.sh .
  PASS: 10 integration tests; clean schema 0004
docker compose ... build api web
  PASS: API image; Next.js 16.2.11 production build and TypeScript
python tests/retrieval/evaluate.py  # isolated DB, real Ollama
  PASS: baseline unchanged; no-answer and stale exclusion passed
playwright test  # restored isolated DB, API 18010, Web 13010
  PASS: 4/4 scenarios
bash scripts/backup-wsl-docker.sh
bash scripts/restore-test-wsl-docker.sh <new backup>
  PASS: checksum and separate PostgreSQL restore
```

Retrieval baseline remained:

| mode | Hit@5 | Hit@10 | MRR | filter | citation |
|---|---:|---:|---:|---:|---:|
| keyword | 0.6667 | 0.6667 | 0.6667 | 1.00 | 1.00 |
| semantic | 1.00 | 1.00 | 0.8519 | 1.00 | 1.00 |
| hybrid | 1.00 | 1.00 | 0.9259 | 1.00 | 1.00 |

The production-data Playwright attempt passed 2/4 but correctly found no Activity fixture and no
retryable failed job after cleanup. Tests were redesigned to restore a backup into an ephemeral
tmpfs database, seed validation-only rows there, and run on isolated ports. The isolated run passed
4/4 and left production activity count at zero. Earlier integration/retrieval orchestration attempts
also failed closed on the dedicated-DB guard, PostgreSQL 18 tmpfs path, PowerShell hostname
interpolation, and model-host allowlist; each was corrected without weakening the guard.

Final append-only backup:

- path: `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T160749Z`
- status: `succeeded`
- dump size: `27,115,408` bytes
- SHA-256: `f0c50a5b09f0d50908c53663399939820b3fd53aa7decb44579ad7cd492de9c6`
- schema: `0004_project_semantic_scope`
- restore result: documents 3,817, chunks 39,775, vectors 286, activities 0

Verified canonical cases: 2, distinct dedup keys: 2, exact duplicates: 0. The only remaining manual
approval is Codex `/hooks` trust review; status remains `MANUAL_APPROVAL_REQUIRED`.

Verdict for this correction: **VERIFIED**. No long-duration endurance or thermal soak is claimed.

## 2026-07-24 menu, search latency, CPU budget, and hook trust correction

### Implemented

- Renamed the navigation item from `문서 탐색` / `Explorer` to `저장소 탐색` /
  `Repository explorer`. The page remains `저장소와 파일`: code belongs in this read-only
  repository catalog, while only purpose-selected Markdown is embedded by default.
- Replaced the combined lexical sequential scan with indexed full-text, path, and symbol candidate
  stages. Added migrations `0005_search_candidate_indexes` and
  `0006_chunk_content_trigram`.
- Split word-similarity and literal-content lookup into fallbacks that run only when the indexed
  stage returns no candidate. This avoided a measured 4.5-second common-term regression while
  retaining Korean and paraphrase retrieval.
- Added a five-second per-search PostgreSQL statement timeout.
- Added a revision-aware 512-entry TTL/LRU query-embedding cache, 24-hour Ollama keepalive,
  startup prewarm, bounded 30-second provider timeout, and cache statistics in readiness and
  dashboard metrics.
- Added last-hour keyword/semantic/hybrid p50/p95 metrics to the dashboard.
- Corrected duplicate example values that could have made a new deployment use two Ollama CPUs.
- Applied cgroup budgets: Ollama/worker/API `1.0` CPU; watcher/hook collector/web `0.5` CPU.
  Memory and PID limits are also explicit.
- Documented the evidence gate for any future 1.5-CPU experiment in ADR 0008. Two CPUs are not a
  default.
- Corrected global-hook spool placement to the workstation-approved
  `E:\LocalKnowledgePortal\ingest\codex-spool` and mounted that exact path into the collector.

### Executed verification

```text
bash scripts/validate-container.sh .
  PASS: ruff; 32 unit tests
LKP_TEST_DATABASE_URL=<ephemeral tmpfs PostgreSQL 18> \
  bash scripts/test-integration-container.sh .
  PASS: 10 integration tests; one upstream Starlette deprecation warning
docker compose ... build api web
  PASS: API image; Next.js 16.2.11 production build and TypeScript
python tests/retrieval/evaluate.py  # isolated DB, real Ollama
  PASS: no-answer, stale exclusion, filters, and citations
browser verification against http://127.0.0.1:3010
  PASS: Korean overview, Repository explorer menu, 3,817-file project tree,
        live model/cache/latency metrics, PostgreSQL and Ollama health
bash scripts/backup-wsl-docker.sh
bash scripts/restore-test-wsl-docker.sh <new backup>
  PASS: checksum and separate PostgreSQL 18 restore
```

The complete isolated Playwright 4/4 run recorded earlier in this report remains the full E2E
baseline. This correction additionally performed a live browser check of the changed menu and
dashboard surfaces; it did not claim a second full fixture-seeded Playwright run.

### Performance and resource evidence

Production data: 3,817 current files, 39,758 current chunks, 269 semantic chunks.

| Query path | Measured latency |
|---|---:|
| common keyword, first after API restart | 191 ms |
| common keyword, warm | 13 ms |
| unique semantic, model warm / cache miss | 1,372 ms |
| repeated semantic / cache hit | 12 ms |
| unique hybrid, model warm / cache miss | 1,183 ms |
| repeated hybrid / cache hit | 13 ms |

Five sequential semantic cache misses completed in `998–1,501 ms`; active Ollama samples were
`76–88%` of its one-CPU quota. The final idle snapshot was Ollama `0.00%`, worker `0.24%`, API
`0.14%`, watcher `0.14%`, hook collector `0.00%`, and web `0.00%`. The loaded model used about
`1.29 GiB` of its 6-GiB limit.

The one-CPU model profile therefore remains the safe default. It is not declared permanently
optimal: increasing it requires a representative workload, a real package-temperature sensor,
user-approved thermal bound, 30-minute soak, and rollback evidence. Those endurance/thermal tests
remain outside this run.

### Retrieval evaluation

| mode | Hit@5 | Hit@10 | MRR | filter | citation |
|---|---:|---:|---:|---:|---:|
| keyword | 0.8889 | 0.8889 | 0.8889 | 1.00 | 1.00 |
| semantic | 1.00 | 1.00 | 0.8519 | 1.00 | 1.00 |
| hybrid | 1.00 | 1.00 | 0.9259 | 1.00 | 1.00 |

Keyword retrieval improved from the previous `0.6667` baseline; semantic and hybrid did not
regress.

### Hook trust and spool evidence

The user changed all displayed hooks to trusted during this task. After that change, live activity
contained `UserPromptSubmit`, `PostToolUse`, and `Stop`; every row remained `UNVERIFIED`.
`SessionStart` cannot occur retroactively in the current session, and subagent events occur only
when a subagent is used. The earlier isolated hook validation in this report already exercised all
six configured event types.

After the approved spool-path correction, live hook files appeared only under
`E:\LocalKnowledgePortal\ingest\codex-spool`. The collector mount resolved to
`/mnt/e/LocalKnowledgePortal/ingest/codex-spool -> /hook-spool`; pending and processing both drained
to zero, and the activity total advanced. No API, database, or remote model call occurs in the hook
process.

### Backup and current state

- backup: `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-23T164702Z`
- restored schema: `0006_chunk_content_trigram`
- restored rows: 3,817 documents, 39,775 chunks, 286 vectors, 71 activities
- live API/web/PostgreSQL/Ollama/worker/watcher/hook collector: healthy
- pending initial/live jobs: `0 / 0`

Verdict for this correction: **VERIFIED**. The only excluded work is long-duration endurance and a
sensor-backed thermal soak; neither is represented as passed.
