# Validation report

검증 시각: 2026-07-24 14:09 KST

저장소: WSL2 ext4 `/home/kutae/src/local-knowledge-portal`

최종 판정: **VERIFIED**

장시간 watcher endurance, 장시간 지속 부하, 실제 Windows 장시간 절전/복귀만 제외했다.
그 밖의 코드, migration, 운영 서비스, GPU 예약, 검색, 웹, backup/restore를 실제로
실행했다. 실행하지 않은 항목을 성공으로 기록하지 않았다.

## 구현 기준 상태

- 소스: WSL2 ext4 `/home/kutae/src/local-knowledge-portal`
- 운영 Compose/config: `C:\Docker\local-knowledge-portal`
- 애플리케이션 데이터: `E:\Data\LocalKnowledgePortal`
- embedding model: `E:\AI\Models\Ollama`
- generation model: `E:\AI\Models\Ollama\generation\models`
- append-only backup: `D:\LocalBackup\LocalKnowledgePortal`
- PostgreSQL: 18.4, pgvector 0.8.2
- Alembic head: `0006_chunk_content_trigram`
- pipeline: `1.2.0`

PostgreSQL은 Docker Desktop의 named volume을 사용하며 원본 저장소와 Vault의 사람이
작성한 문서는 수정하지 않는다. 생성 페이지는 `_generated` 아래에만 기록한다.

## 이번 완주에서 구현한 내용

### 코드가 소유하는 지식 파이프라인

지식 후보 상태, evidence gate, 게시 자격, exact dedup, occurrence, revision, 프로젝트·태그,
인용 표기, DB 갱신, 사례 페이지와 프로젝트 overview 갱신은 결정론적 코드가 소유한다.
LLM의 `decision`은 편집 제안일 뿐 상태 전이 권한이 없다.

- 최소 글자 수와 분량 채우기를 제거했다.
- 문제, 원인 또는 결정, 구현, 검증은 evidence-bound 필수 구조로 유지한다.
- 선택적 맥락·한계는 근거가 없으면 억지로 채우지 않는다.
- 과장 가능 표현은 soft warning이며, 확인되지 않은 주장·숫자는 본문에서 제외하고
  metadata의 검토 항목으로 보존한다.
- 단순 CSS 변경은 activity only다.
- 실패→원인 수정→성공은 verified 오류 해결 후보가 된다.
- 동일 문제·원인·해결은 새 case 대신 occurrence/revision을 추가한다.
- 같은 증상·다른 원인은 별도 case와 relation 대상이다.
- before/after와 load cause가 없는 성능 주장은 게시하지 않는다.
- Codex 성공 보고만 있고 실행 evidence가 없으면 `UNVERIFIED`다.

운영 DB에는 verified current case 3개, retired historical case 6개가 있다. 전체 case 9개,
occurrence 10개, revision 13개이며 duplicate dedup group은 0개다. candidate는
`needs_review=12`, `activity_only=7`, `published=4`다. published candidate 4개 중 하나는
기존 case의 occurrence로 병합되었다.

### 프로젝트 위키와 검색 taxonomy

사례를 `project`, `case`, `situation`, `lifecycle`, `knowledge-value` 다중 태그로
인덱싱하고 프로젝트·여러 태그의 all/any 필터를 구현했다.
`_generated/Projects/local-knowledge-portal/overview.md`는 현재 verified case로
결정론적으로 갱신한다.

동일 content hash 재수집은 새 document version이나 vector를 만들지 않지만
frontmatter 프로젝트·태그는 다시 동기화하도록 worker의 조기 반환 결함을 수정했다.
운영의 사례 3개와 overview 1개를 append-only metadata resync job으로 재처리했다.
결과는 `project:local-knowledge-portal=4`, 프로젝트 facet `local-knowledge-portal=4`이며
기존 version ID는 유지됐다.

파일 탐색의 code file은 원본 카탈로그이므로 정상이다. Knowledge Case는 별도 검증 층이며
일반 코드 파일 존재만으로 사례가 되지 않는다. 관련 결정은 ADR 0015에 기록했다.

### 로컬 LLM과 GPU scheduler

Production evidence editor:

- provider: Ollama
- model: `qwen3.5:9b-q4_K_M`
- digest:
  `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`
- prompt: `evidence-blog-v9`
- deterministic harness: `evidence-gate-v3`
- generation revision:
  `c002128dc53e5196fe22416123474da760206a0b5f981f76d9f2bbe729edb106`

Qualification `knowledge_curator.qualification.00200c1bd2e8bc1d7305db7a`는 `PASS`다.
지원되는 구현 사례는 1,412자 evidence-bound 글로 편집됐고, reported-only와 측정 없는
성능 사례는 각각 `no_verified_evidence`,
`before_after_and_load_cause_required`로 보류됐다.

2 GiB 이상 또는 30초 이상 예상되는 curation은 직접 실행하지 않는다.
`scripts/curate.ps1`가 `gpuq run --vram 8192 --eta 45 --priority 40`으로 제출하고,
동일 workload가 active/queued이면 중복 제출하지 않는다. 기본 Compose의 persistent
curator는 `manual-curation` profile의 one-shot으로 바꿨다.

`\LocalKnowledgePortal\CurateKnowledge` 작업을 60분 간격, `IgnoreNew`로 등록했다.
수동 검증 실행과 첫 예약 실행의 `LastTaskResult`는 0이다. 실제 GPU queue job:

- qualification: `a2f23a28-e6ab-4ede-a3cb-cba04d59bc5e`, exit 0,
  peak total GPU 12,156 MiB
- scheduled smoke: `dc4df102-652e-480a-a34b-db88c1fb7c94`, exit 0
- first hourly run: `bbd9b52f-4cae-49ec-9e7f-da072eb63d20`, exit 0

마지막 확인 시 RTX 5090은 total 32,607 MiB, used 4,885 MiB, free 27,303 MiB,
utilization 5%, active/queued GPU job 0개였다.

### GPU Queue 포털 통합

FastAPI는 다음 GET만 host scheduler로 전달한다.

- `/api/v1/gpu-queue/health`
- `/api/v1/gpu-queue/status`
- `/api/v1/gpu-queue/jobs/{uuid}`

upstream은 loopback 또는 `host.docker.internal`만 허용한다. POST와 scheduler token은
포털에 노출하지 않는다. `/gpu-queue`에서 GPU total/used/free/utilization,
active/queued/completed, effective score, scheduling note와 작업 상세를 한/영으로 표시한다.
실제 health/status/job은 HTTP 200, POST는 405였다.

### 장기 운영 worker 상태

컨테이너 재생성마다 임의 worker ID를 만들던 설계 때문에 47개 과거 row가 영구
`stale`로 보이는 결함을 수정했다.

- 현재 worker ID는 설정 가능한 `worker-service:primary`다.
- 이전 ephemeral row는 삭제하지 않고 `stopped`, `retired=true`,
  `superseded_by`로 보존한다.
- `/api/v1/workers` 기본값과 dashboard는 retired history를 제외한다.
- 감사 조회는 `include_retired=true`로 가능하다.

현재 worker 집계는 `idle=1`, `healthy=5`, stale 0, pending 0이다.

## Codex 전역 hook과 Activity

`C:\Users\kutae\.codex\hooks.json`에는 `SessionStart`, `UserPromptSubmit`,
`PostToolUse`, `Stop`, `SubagentStart`, `SubagentStop` 여섯 이벤트가 있다. hook은
API/DB/model을 호출하지 않고
`E:\Data\LocalKnowledgePortal\ingest\codex-spool`에 bounded atomic envelope만 기록한다.
fallback, redaction, deterministic idempotency, malformed/oversized quarantine와 collector
재처리를 구현했다.

최신 Activity에서 현재 task 외에 별도 session
`019f8d9d-2692-7c41-8acb-c9219387ddc5`,
project `gh-computer-use-android-rtx-5090`의 Stop event도 확인했다. 따라서 한 채팅에
국한되지 않고 전역 hook에서 수집된다. 낮은 신호 lifecycle·짧은 응답·read-only
도구는 저장 전에 버리고, 선택된 activity detail은 30일 후 logical roll-up한다.
activity는 임베딩 문서가 아니며 evidence gate를 통과하기 전에는 case가 아니다.

사용자는 `/hooks`에서 신뢰로 변경했다고 명시했다. runtime marker를
`USER_CONFIRMED`, `USER_ATTESTATION_ONLY`로 갱신했다. Codex 제품 내부 trust 상태는
외부 코드로 읽거나 우회할 수 없으므로 machine verification은 불가능하다고 명시한다.

## 실제 검증 명령과 결과

```text
.venv/bin/ruff check services scripts tests
  PASS: All checks passed

.venv/bin/pytest -q tests/unit
  PASS: 66 passed

docker build -f infra/docker/Dockerfile.test ...
docker run ... pytest -q tests/integration
  PASS: 19 passed, 1 Starlette dependency deprecation warning

docker compose ... build api web
  PASS: API image, Next.js 16.2.11 production build, TypeScript

docker run --network host mcr.microsoft.com/playwright:v1.61.1-noble ...
  PASS: 5 passed in 8.2s

uv run python tests/retrieval/evaluate.py
  PASS: dedicated disposable DB, real Ollama embedding

bash scripts/backup-wsl-docker.sh
  PASS: 2026-07-24T050826Z

bash scripts/restore-test-wsl-docker.sh \
  /mnt/d/LocalBackup/LocalKnowledgePortal/database/2026-07-24T050826Z
  PASS: separate temporary PostgreSQL restore and integrity checks
```

Playwright는 한/영 전환과 유지, Overview freshness, 저장소 트리, 문서 version/diff,
keyword/semantic/hybrid, provenance, Activity 목록·상세, Knowledge Case 목록·상세,
Candidate review, failed job retry, worker heartbeat, backup, GPU Queue와 개별 작업을
검증했다.

통합 테스트에는 queue lease 회수, watcher create/modify/rename/delete, reconciliation,
중복 event, 삭제/복원, model revision/dimension guard, evidence gate, dedup/relationship,
global activity, 동일 hash metadata resync, legacy worker retirement가 포함된다.

## Retrieval baseline

4문서 Korean/English/code 전용 corpus와 실제 production embedding revision으로 측정했다.
목표치를 미리 성공 기준으로 만들지 않고 관측 baseline을 기록했다.

| Mode | Hit@5 | Hit@10 | MRR | Filter | Citation |
|---|---:|---:|---:|---:|---:|
| Keyword | 0.8889 | 0.8889 | 0.8889 | 1.00 | 1.00 |
| Semantic | 1.00 | 1.00 | 0.8519 | 1.00 | 1.00 |
| Hybrid | 1.00 | 1.00 | 0.9259 | 1.00 | 1.00 |

no-answer correctness와 stale document exclusion도 통과했다. production embedding:

- model: `qwen3-embedding:0.6b`
- digest:
  `ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d`
- dimension: 1024
- revision: `ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1`

deterministic validation vector revision은 production과 분리한다.

## Backup/restore

최종 immutable backup:

- directory: `D:\LocalBackup\LocalKnowledgePortal\database\2026-07-24T050826Z`
- dump size: 34,814,652 bytes
- SHA-256:
  `bb9aa0a8d6a1dfb092d62bb1cefff924f71edc0c557b2d478437239c6472a194`
- source configuration hash:
  `91b812b5413258701bcce7b53f9c24f2f1b168b5cf01ecf482f89ab5afb8a6fb`
- schema: `0006_chunk_content_trigram`
- managed Vault files: 12

별도 임시 DB restore 결과는 documents 4,390, chunks 41,864, vectors 529,
activities 1,798이다. 이 수치는 retired/validation/history를 포함한 backup 전체이며
dashboard의 production active 수치와 다르다. checksum과 restore가 성공한 뒤 임시 DB는
제거했다.

## 현재 실행 상태와 부하

- API `127.0.0.1:8010`: ready, PostgreSQL true, Ollama true
- Web `127.0.0.1:3010`: HTTP 200
- production active: projects 7, documents 4,015, chunks 40,709
- semantic chunks 324, coverage 0.7959%; repository code는 의도적으로 lexical-only
- pending initial/live jobs: 0/0
- last-hour failed jobs: 0
- workers: idle 1, healthy 5, stale 0
- measured CPU:
  - Ollama 3.55%, hard limit 0.5 CPU
  - worker 0.29%, hard limit 1 CPU
  - watcher 0.91%, hard limit 0.5 CPU
  - API 0.13%, hard limit 1 CPU
  - web 0.00%, hard limit 0.5 CPU
- search last-hour p95: keyword 9.85 ms, semantic 13 ms, hybrid 1,161.8 ms

초기 전체 코드 카탈로그를 embedding하지 않는 `docs_only` 정책 때문에 semantic coverage가
낮다. 이는 CPU 보호와 검색 노이즈 억제를 위한 의도된 경계이며 lexical/path/symbol
검색은 전체 카탈로그를 사용한다.

## 실패·교정·생략

- 첫 full Playwright를 `host.docker.internal` page origin으로 실행해 브라우저 내부
  `127.0.0.1` API가 offline이 됐다. 앱 결함으로 숨기지 않고 검증 명령을 운영과 같은
  `--network host`, loopback origin으로 교정해 5/5를 재실행했다.
- retrieval DB의 첫 migration에서 `LKP_TEST_DATABASE_URL`만 설정해 Alembic이 기본
  `127.0.0.1:55432`를 찾다가 실패했다. 전용 DB의 `LKP_DATABASE_URL`을 명시하고
  clean migration과 평가를 다시 성공시켰다.
- 저장소 안에 root 소유 `.pnpm-store` 1.1 MiB가 남았다. `.gitignore`에 추가하고
  일반 사용자 trash 권한 실패를 확인한 뒤 root WSL trash로 이동했다. 복구 가능하며
  영구 삭제하지 않았다.
- `StartAtLogon` 검증 직후 watcher 외부 CPU가 47.38%로 측정됐다. startup
  reconciliation과 grace 구간을 지속 과부하로 오판하지 않고 20초 후 다시 측정했다.
  watcher는 0.91%, 내부 sample 0.36%, `cpu_alert=false`, `healthy`로 안정됐고 pending
  job은 0이었다.
- 장시간 watcher endurance, 장시간 지속 부하, 실제 Windows 장시간 절전/복귀는
  이번 범위에서 실행하지 않았다.

## 2026-07-24 자동 선별 전체 스냅샷 교정

기존 curator는 후보 목록을 조회한 뒤에도 `knowledge_curation_batch_size=1`에서 루프를
중단했다. 이 제한을 제거하고 advisory lock 획득 직후의 eligible candidate ID를
스냅샷으로 고정했다. 실행 도중 생성되거나 새로 eligible이 된 후보는 다음 예약으로
넘기며, 시작 시점 스냅샷은 전부 시도한다. 각 후보는 savepoint로 격리하여 한 후보의
모델 출력 오류가 뒤 후보 처리를 막지 않게 했다. scheduler state에는
`snapshot_candidate_count`, `processed_candidate_count`, `changed_candidate_count`,
`unchanged_candidate_count`, `failed_candidate_count`를 남긴다.

one-shot 예약 작업이 상주형 curator의 `next_attempt_at`까지 존중해 한 시간 전체를
건너뛸 수 있던 충돌도 교정했다. 외부 예약 실행은 매번 GPU를 새로 검사하고,
내부 backoff 시각은 선택적 persistent loop에서만 poll 억제에 사용한다.

실제 실행과 결과:

```text
uv run ruff check services tests
  PASS: All checks passed

uv run pytest -q tests/unit
  PASS: 66 passed

dedicated lkp_test_curator_snapshot_20260724_03
  PASS: 19 integration tests
  포함: 시작 전 후보 3건 전체 처리, 시작 경계 뒤 후보 1건 다음 실행으로 보류

docker compose ... build api
  PASS: local-knowledge-portal-app:wsl

docker compose ... build web
  PASS: Next.js production build and TypeScript

Scheduled Task \LocalKnowledgePortal\CurateKnowledge
  installed: hourly, IgnoreNew
  gpuq reservation: 8192 MiB, ETA 1800 seconds, max runtime 21600 seconds

operational gpuq job 878c1bb9-3acb-4ac4-b820-a24295d0645a
  PASS: exit code 0
  snapshot candidates: 13
  processed candidates: 13
  changed decisions: 10
  unchanged decisions: 3
  failed candidates: 0

automatic scheduled gpuq job c4961f03-2017-4b28-9e74-2e6e22f2bb8f
  PASS: 2026-07-24 14:38:38 KST trigger, exit code 0
  snapshot/processed: 13/13
  unchanged: 13
  failed: 0
  next scheduled run: 2026-07-24 15:38:38 KST
```

첫 운영 재실행 job `b52b435c-36d3-4877-bc59-34b7d696a828`은 admission 직후
순간 GPU utilization 50%를 관측하여 내부 안전장치가 `waiting_for_gpu`로 보류했다.
후보를 처리했다고 거짓 보고하지 않고, one-shot/backoff 충돌을 교정한 뒤 GPU 2%에서
다시 실행하여 위 13/13 결과를 확인했다.

웹의 기존 `자동 선별 대기·보류` 문구는 처리 전후를 구분하지 못했다. 이를
`승격 보류·근거 보완`으로 바꾸고 최근 스냅샷 처리/전체/실패 수를 표시한다. 후보
상세도 curation metadata가 없을 때만 대기로, 자동 판정이 있으면 근거 부족에 따른
승격 보류로 설명한다. 현재 `needs_review` 13건은 미처리 대기열이 아니라 자동 선별을
마쳤으나 게시 근거가 부족한 보존 기록이다.

## Rollback

`./scripts/docker-stack.sh stop`으로 서비스를 중지한다. named volume과 E: data는
보존하며 직접 복사·수정·삭제하지 않는다. 최종 custom-format dump를 새 PostgreSQL
DB에 먼저 복원하고 schema/count/search를 확인한 뒤에만 설정을 전환한다. 검색 index와
vector는 재생성 가능하고 원본 source root는 어떤 단계에서도 이동·이름 변경·삭제하지
않았으므로 source rollback은 필요 없다.

## 최종 판정

장시간 endurance와 실제 장시간 절전 테스트를 제외한 필수 기능, 자동화, 로컬 LLM
qualification, GPU 예약, 한/영 웹 UX, 운영 복구, 검색 평가, backup/restore와 실행
증거가 모두 검증됐다.

**최종 판정: VERIFIED**

## 2026-07-24 프로젝트 개발 일지·장기 운영 보강

재사용 지식 가치 게이트가 주요 프로젝트 변경까지 `activity_only`로 숨기던 경계를
분리했다. `project_journal_entry`는 정식 지식 사례와 독립적으로 사용자 의도, 주요
변경 보고, 변경 파일, 실패/성공 실행 근거, 해결 설명, 실제로 관측된 RAG provenance를
저장한다. 단일 presentation-only 변경은 계속 활동 이력에만 남는다. 프로젝트별
각 항목은 `_generated/Projects/<project>/Journal/` 아래 불변 문서로 한 번만 기록하고,
`_generated/Projects/<project>/development-journal.md`는 현재 항목을 연결하는 소형
index로만 atomic write 및 indexing한다.

실제 backfill 결과:

- journal entries: 17
- projects with managed journals: 5
- 요청에서 지적한 `[local-knowledge-portal] 수정·운영 검증 완료했습니다.`:
  `VERIFIED` journal로 backfill됨
- generated page:
  `/data/vault/_generated/Projects/local-knowledge-portal/development-journal.md`
- schema revision: `0007_project_journal_pagination`
- 사람 검토로 오해되던 `NEEDS_REVIEW` 한국어 label:
  `자동 승격 보류`로 변경

목록 조회에는 정렬 tie-breaker와 복합 index를 추가했고 Activity, Knowledge Case,
Candidate, Project Journal 화면에 서버 페이지 번호·총건수·이전/다음 제어를 연결했다.
새로운 Windows repository collection 예시는 `watch_mode: disabled`,
`reconcile_interval_seconds: 3600`으로 두어 Docker bind mount를 재귀 polling하지
않으면서 새 repository를 주기적으로 발견한다. 운영 WSL source와 Vault는 각각
native/polling, 900초 reconciliation으로 실행 중이다.

노이즈 격리 전후 production current 수치:

| 지표 | 전 | 후 |
|---|---:|---:|
| active documents | 4,015 | 2,297 |
| current search chunks | 40,709 | 25,317 이하 |
| ignored documents | 375 수준 | 2,093 |
| active chunks over 10,000 chars | 124 관측 | 0 |

`third_party`, generated documentation search bundle, CUTLASS test hash cache, legacy debug JSON을
삭제하지 않고 `ignored`로 전환했다. 한 physical line이 6,000자를 넘을 때 line provenance와
character offset을 보존해 분할한다. 프로젝트 journal backfill 중 생성된 superseded pending
index job 15건은 행을 삭제하지 않고 `cancelled`와 원인을 기록했다. 이후 enqueue는 같은
path의 pending snapshot을 coalesce하고, lease가 남은 오래된 job도 더 최신 snapshot이 있으면
처리 전에 cancelled로 보존한다.

실행 검증:

```text
uv run ruff check .
  PASS: All checks passed

uv run pytest tests/unit -q
  PASS: 70 passed

dedicated lkp_test_project_journal_20260724_04
  PASS: 20 integration tests
  포함: clean migration, journal materialization, lease recovery,
        superseded snapshot cancellation

pnpm --filter web build
  PASS: Next.js 16.2.11 production build and TypeScript

docker compose ... build api web
  PASS

GET /health/ready
  PASS: ready, schema 0007_project_journal_pagination, Ollama true

watcher after reconciliation
  Docker CPU 0.14-0.15%, memory about 92 MiB
  internal process_cpu_percent 0.4%, cpu_alert=false

server-paged API probes
  tree 2/2297, jobs 2/4511, workers 2/6, backups 2/16,
  timeline 2/7074, document chunks 2/12, versions 1/1

Playwright 1.61.1, loopback production services
  PASS: 5/5 in 7.0s
  포함: project journal detail, pagination controls, search modes,
        explorer/document diff, operations/worker, provenance

backup 2026-07-24T061546Z
  PASS: dump 35,043,457 bytes
  SHA-256 f786a937f2acbe311dcb8e005d7cb217a928e32013f15afd4ecfa79dfd6aa54d
  schema 0007_project_journal_pagination, managed Vault files 34

restore-test 2026-07-24T061546Z
  PASS: separate temporary DB
  documents 4,390, chunks 41,864, vectors 529, activities 1,955
```

장시간 endurance와 실제 Windows 절전 장시간 시험은 기존 명시적 제외 범위 그대로다.

개발 일지는 장기 재임베딩 비용을 줄이기 위해 17개 불변 entry 문서와 프로젝트별
소형 current index로 최종 분리했다. `local-knowledge-portal` index는 16,792 bytes의
전체 본문 누적형에서 5,345 bytes 링크형으로 감소했다. 이후 신규 주요 작업은 entry
한 건과 index 한 건만 갱신하며, 과거 entry를 다시 작성하지 않는다.

`C:\Dev\Repos`는 직접 하위 directory 30개 중 Git repository 17개로 inventory했다.
Watcher container의 `/sources/windows-repositories` mount는 `RW=false`로 검증했다.
아직 active source root에는 추가하지 않아 미승인 저장소를 대량 색인하지 않는다.
활성화할 때 `repository_collection`은 direct-child Git repository만 scan하고,
unrelated directory와 nested dependency repository를 root로 취급하지 않는다. 이
source type도 기존 `docs_only` semantic policy를 적용하도록 회귀 결함을 수정했다.
따라서 향후 저장소 수가 늘어도 code 전체가 production embedding 대상으로 바뀌지
않는다.

```text
repository_collection regression
  PASS: direct-child Git repositories only
  PASS: .git directory and Git worktree .git file
  PASS: repository code remains repository_docs_only
  PASS: inaccessible collection root returns a bounded scan error

runtime resource snapshot during one-time journal embedding
  worker: 1 CPU hard limit, 0.00-0.27% observed
  watcher: 0.5 CPU hard limit, 0.65-0.79% observed
  Ollama embedding: 0.5 CPU hard limit, 43.70-49.80% container quota observed
  RTX 5090: 38 C, 3% utilization, 24,858 MiB used, 7,330 MiB free
```

Ollama의 약 50% 표시는 호스트 전체 CPU 50%가 아니라 `0.5 CPU` quota 안에서의
container 수치다. 저장소가 추가돼도 worker 수를 자동 증식하지 않는다. 초기 backlog는
coalescing, workload별 cooldown, model concurrency 1, container CPU/memory limit로
제어하고 queue latency와 oldest pending age를 근거로만 명시적으로 확장한다.

최종 배포 후 일회성 journal backlog는 모두 소진됐다. 현재 queue는
`succeeded=4,499`, `cancelled=34`, pending/processing/failed/dead-letter `0`이다.
운영 production은 active document `2,319`, current lexical/search chunk `25,424`,
현재 embedding revision vector `468`이다. 문서 상태 기준으로 semantic embedding
완료 문서는 `47`, 정책상 lexical-only 문서는 `2,272`이며 후자는 code catalog를
semantic knowledge로 부풀리지 않는다.

프로젝트 목록도 server pagination을 적용했다. 실제 응답은 page size `2`, project
total `10`, production document total `2,319`였고 document total은 JSON integer로
확인했다. 프로젝트 페이지와 각 프로젝트의 document 페이지는 독립적으로 이동한다.
Search는 exhaustive catalog list가 아니라 의도적으로 bounded top-k retrieval이고,
그 top-k 결과 안에서 virtualized page를 제공한다.

최종 Playwright 재실행은 `5 passed (8.5s)`였다. 첫 직접 재실행은 기존 root-owned
generated `apps/web/test-results/.last-run.json`에 쓸 수 없어 `EACCES`로 실패했다.
파일을 삭제하지 않고 generated test-results directory의 소유권만 repository user로
복구한 뒤 같은 명령을 두 번 성공시켰다. 최종 API readiness는
`ready`, schema `0007_project_journal_pagination`, web root는 HTTP `200`이다.

키워드 검색은 실제 반복 측정에서 다중어 exact FTS가 0건일 때
`word_similarity` 후보 전체 정렬로 내려가 `4.45-4.70초`가 걸리는 병목을 확인했다.
최종 순서는 exact indexed FTS → 두 단어 이상 일치하는 bounded pair FTS → 짧은 단일어
exact phrase/fuzzy로 제한했다. `websearch_to_tsquery`가 `AND` 문자열을 연산자가
아닌 검색어로 파싱하는 문제를 제거하도록 pair는 인접 term 문법으로 생성하고 unit
test로 고정했다.

최종 운영 측정:

```text
keyword "Ollama CPU 안전": 100 ms cold, 3 results
keyword "PostgreSQL queue": 12 ms warm, 3 results
keyword no-answer Korean multi-term: 14 ms, 0 results, confidence none
semantic "Ollama CPU 안전": 1,678 ms cold embedding, 3 results, confidence high
hybrid same query: 26 ms cached, 3 results, confidence high
```

검색 결과에는 계속 source path, document/version/chunk id, line range, content hash,
indexed timestamp와 match reason이 포함된다.

## 2026-07-24 프로젝트 지식 정보 구조·UTF-8·서비스 상태 보강

최종 판정: `VERIFIED`

개발 일지 모지베이크의 원인은 Windows PowerShell 훅 wrapper가 redirected stdin의
인코딩을 명시하지 않아 UTF-8 Stop payload를 활성 code page로 해석한 것이었다.
repository와 실제 설치 wrapper 모두 입력·출력을 BOM 없는 UTF-8로 고정했다. Collector는
Stop/SubagentStop의 보고 결과를 hook payload보다 Codex UTF-8 transcript의 동일 turn
`task_complete.last_agent_message`에서 우선 읽는다.

기존 운영 데이터는 원본 파일을 건드리지 않고 다음처럼 복구·격리했다.

- 손상된 `gpu-workload-scheduler` stop, project journal, candidate를 transcript에서 복구
- 복구된 journal index를 atomic UTF-8 write로 다시 materialize
- 추가 손상 Stop 2건을 transcript에서 복구
- exact task-complete가 없는 legacy activity-only 1건은 삭제하지 않고 `rolled_up`으로 격리
- 최종 visible activity, journal title/summary, candidate result의 mojibake 탐지: 모두 0

포털 정보 구조는 데이터 경계를 기준으로 분리했다.

- `원본 파일`: repository와 사람이 작성한 문서만 표시하고 Obsidian `_generated` 제외
- `통합 검색`: source와 managed knowledge를 provenance와 함께 조회
- `Codex 작업`: 활동과 실행 근거
- `프로젝트 지식`: 프로젝트 → 작업 특성 트리, 태그 filter, 최신 갱신 순
- `수집·운영`, `변경 기록`, `문서 관계`: queue/heartbeat, ingest event, link graph

정식 tag key는 기존 영어 canonical value를 유지해 API·index 안정성을 보존하고, 한국어
UI에서는 `사례: 성능·부하`, `작업 특성: 운영·장애`, `상태: 검증됨`,
`프로젝트: <name>`으로 표시한다. API도 한국어 filter alias를 canonical tag로
정규화한다. `knowledge_case`에는 project/category/latest partial B-tree와 tags GIN
index를 추가했고 schema revision은 `0008_knowledge_navigation`이다.

우측 연결 상태는 `/api/v1/system/services`에서 Docker socket 노출 없이 persistent
service를 확인한다. 실제 운영 응답은 web, FastAPI, PostgreSQL, embedding Ollama,
knowledge-editor Ollama, worker, watcher, reconciler, hook collector, host GPU scheduler
10개였고 모두 `healthy`였다. Hook collector heartbeat도 새로 기록한다.

검색은 ingestion이 Ollama를 점유한 경우 semantic/hybrid 요청이 30초까지 UI를 막던
문제를 확인했다. query embedding timeout을 5초로 제한하고 실패 시 응답 mode를
`semantic-degraded-keyword-only` 또는 `hybrid-degraded-keyword-only`로 명시한 뒤
lexical provenance를 반환한다. 운영 부하 중 측정은 각각 약 5.03초, 결과 5건,
confidence `low`였다. API 시작 예열도 background task로 전환해 readiness가 예열
timeout을 기다리지 않는다.

실제 실행·검증:

```text
uv run ruff check services tests db
  PASS: All checks passed

uv run pytest tests/unit -q
  PASS: 72 passed

dedicated DB lkp_test_navigation_20260724
  PASS: 20 integration tests, 1 upstream deprecation warning
  포함: clean migration through 0008, queue/recovery/indexing regressions

docker compose ... build api web
  PASS: API image and Next.js 16.2.11 production/TypeScript build

Playwright 1.61.1 against loopback production services
  PASS: 5/5 in 25.4s
  포함: 한/영 메뉴, source explorer, project/work-type knowledge tree,
        journal/candidate/case, keyword/semantic/hybrid degradation,
        operations/worker, provenance, GPU queue

live API probes
  PASS: web HTTP 200
  PASS: system services 10/10 healthy
  PASS: verified cases latest ordered
  PASS: source catalog first 1,000 rows had 0 managed `_generated` leaks
  PASS: source projects 6 / source documents 2,293
  PASS: managed projects 5 / managed documents 33
```

첫 E2E 실행은 기존 30초 hybrid timeout과 새 sidebar 설명으로 인해 모호해진 test
locator 2건을 발견해 실패했다. Timeout/fallback 로직과 exact accessible locator를
수정한 뒤 같은 전체 suite를 재실행해 5/5 통과했다. 장시간 endurance와 Windows
절전 시험은 이번 변경 범위에서도 실행하지 않았다.

## 2026-07-24 Docker 서비스 카탈로그·전체 프로젝트 지식 트리

기존 우측 연결 상태는 Local Knowledge Portal 내부의 고정 10개 probe만 포함해
실행 중인 `unjeong-mining-web` Compose 프로젝트를 발견하지 못했다. Docker
socket을 API 컨테이너에 노출하지 않고 Windows 호스트의 읽기 전용 collector가
`docker ps` 결과 중 비밀값을 제외한 상태·Compose label·port만 30초마다 E 드라이브
runtime에 atomic snapshot으로 기록하도록 변경했다. 로그인 시 자동 시작하는
`\LocalKnowledgePortal\CollectDockerHealth` 작업을 등록했다.

API는 snapshot freshness를 fail-closed로 검사하고 서비스를 `포털 / 프로젝트 웹·API /
공유 인프라` 및 Compose 프로젝트별로 분류한다. 실제 확인 결과는 7개 그룹,
21개 논리 서비스였고 `미닝 운정점 웹사이트`의 app, PostgreSQL, Redis는 모두
Docker healthcheck `healthy`였다. healthcheck가 없는 실행 컨테이너는 정상으로
위장하지 않고 `running · no healthcheck`로 표시한다.

프로젝트 지식은 검증 사례에만 있던 트리를 승격 후보와 프로젝트 개발일지에도
적용했다. 세 탭 모두 `프로젝트 → 작업 특성 → 최신 기록`을 사용하며 project/category
필터와 count/pagination이 서버에서 동일한 조건으로 계산된다. 실제 facet은 검증 사례
1개 프로젝트 3건, 검토 후보 5개 프로젝트 24건, 개발일지 5개 프로젝트 26건이었다.

Codex hook 토큰 영향 감사:

```text
PostToolUse sample hook: exit 0, stdout 0 bytes, spool 332 bytes
current long-running session: 45,422,926 bytes
cumulative input tokens: 564,634,562
cached input tokens: 554,881,841 (98.27%)
```

Hook은 stdout이나 모델용 추가 context를 반환하지 않아 hook 자체가 모델 입력 토큰을
추가하지 않는다. 큰 누적 토큰은 이 장기 작업의 매우 긴 대화·도구 결과 재사용에서
발생했고 대부분 prompt cache에 적중했다. PostToolUse는 로컬 activity spool과
저장량은 늘릴 수 있으나 모델 토큰을 소비하는 지식 주입 경로는 아니다.

실행·검증:

```text
uv run ruff check services tests db
  PASS
uv run pytest tests/unit -q
  PASS: 75 passed
isolated PostgreSQL DB lkp_test_catalog_20260724_1809
  PASS: 20 integration tests, 1 upstream Starlette deprecation warning
corepack pnpm --dir apps/web build
  PASS: Next.js 16.2.11 production build and TypeScript
docker compose -f C:\Docker\local-knowledge-portal\compose.yaml build api web
  PASS
Playwright production E2E
  PASS: 5/5 in 25.6s
Docker inventory monitor
  PASS: scheduled task running; snapshot healthy; age 24 seconds at probe
  measured process CPU: 0.359375 CPU seconds after about 4 minutes
```

첫 통합 실행은 과거 fixture가 남아 있던 `lkp_test_navigation_20260724`를 재사용해
9건이 unique constraint로 실패했다. 해당 DB를 삭제하거나 초기화하지 않고 새 전용
DB `lkp_test_catalog_20260724_1809`를 생성해 clean migration과 전체 suite를
재실행했으며 20/20 통과했다.

최종 판정: **VERIFIED**.

## 2026-07-25 follow-up: restart-safe GPU scheduler and readable project journals

### Scope and truthfulness

This follow-up verifies the scheduler restart repair, journal-presentation
repair, Korean-readable project knowledge view, and their regression tests. It
does **not** reclassify the whole operational queue as healthy: the live queue
snapshot below still has pending, failed, and dead-letter work that requires
normal queue operations and root-cause triage.

### Implemented

- GPU Workload Scheduler restart failure was traced to Windows reserving the
  previous host PostgreSQL port `55440` as an excluded ephemeral range. The
  scheduler now defaults to `55670`, validates host-port reachability after
  Compose startup, recreates only its PostgreSQL container when the host bind
  is absent (retaining the named volume), and supervises API exits with bounded
  backoff. Its DB migration wait is bounded rather than silently hanging.
- Project journals now use a derived presentation layer. It removes injected
  desktop context from visible intent, removes acknowledgement-only completion
  leads, and derives titles in this order: structured goal, cleaned user goal,
  then report text. Original ActivityEvent rows remain immutable.
- A verified material project change may enter the project development journal;
  a generic completion report without a reusable decision now remains
  `activity_only` instead of becoming a knowledge-review candidate.
- A mixed turn no longer makes shared Windows/WSL operational files appear as
  source files of a repository project. Repository-scoped files are displayed
  in the journal and related operational files are retained in entry metadata
  for provenance. Legacy entries were refreshed idempotently.
- Project journal detail now renders safe Markdown-like structure (headings,
  lists, quotes, code fences), line-oriented changed files, execution evidence,
  failures/resolution, and provenance. The portal now has a Korean-friendly
  system font stack with modern Korean and monospace fallbacks, readable line
  height, and responsive journal metadata.

### Evidence executed in this follow-up

```text
uv run ruff check services/indexer/lkp_indexer/activity_knowledge.py \
  services/indexer/lkp_indexer/project_journal.py \
  tests/unit/test_hook_collector_evidence.py \
  tests/integration/test_activity_knowledge.py
  PASS: All checks passed

uv run pytest tests/unit -q
  PASS: 76 passed in 0.53s

isolated PostgreSQL database lkp_test_journal_<timestamp>
  PASS: 21 integration tests in 5.44s
  NOTE: created only for the test and dropped in finally cleanup.

docker compose -f infra/docker/compose.wsl.yaml up -d --build api worker hook-collector web
  PASS: FastAPI image and Next.js 16.2.11 production build/TypeScript completed.

docker run mcr.microsoft.com/playwright:v1.61.1-noble ... pnpm test
  PASS: 5/5 in 35.3s
  Coverage: localized overview, explorer/version/diff/provenance, activity,
  cases/candidates/project journal, keyword/semantic/hybrid search,
  operations/workers/backups, and read-only GPU queue.

python -m lkp_indexer.cli refresh-journal-presentation
  PASS: legacy scope refresh changed 15 entries across 5 projects.
```

The first container E2E attempt was not counted as a success: its browser
origin could not call the localhost-only API. The final run used a test-local
web server on `127.0.0.1:3010`, the allowed CORS origin, with only the API base
set to `host.docker.internal:8010` inside the disposable test container.

### Journal refresh result

```text
project_journal_entry total:                 33
journal_presentation_version=v2:             33
entries with explicit project scope:         15
entries with related operational files split:15
generic acknowledgement titles remaining:    0
```

The refresh is additive and idempotent. Previous visible wording and file
lists are retained in `metadata.journal_presentation_v1`; source activities,
document versions, and raw hook envelopes were not deleted or rewritten.

### Current live snapshot (2026-07-25, after deployment)

```text
API:  127.0.0.1:8010 /health/ready = ready
Web:  127.0.0.1:3010 = HTTP 200
Documents/chunks: 2,603 / 28,529
Repository semantic policy: docs_only
Semantic chunk coverage: 1.85%
Queue: pending 98, failed 136, dead-letter 23, estimated drain about 16.3 h
Workers: 7 registered
```

This is a usable lexical/path/symbol catalogue and a usable evidence-bound
case/journal surface. It is **not yet a fully semantic project-RAG catalogue**:
the deliberate `docs_only` policy leaves most source code lexical-only, and
the active backlog/error counts must be resolved before calling the ingestion
operation healthy at multi-repository scale. The main defect was not proven to
be model size or MoE architecture. The generation model is an evidence-bound
editor; poor turn normalization and cross-project signal mixing were the
observed root causes and are addressed above. Model architecture was not
asserted because no successful model-inspection output was captured here.

### GPU scheduler restart verification

```text
GET http://127.0.0.1:8790/api/health
  PASS: ok=true, last_decision=queue-empty, last_error=null
  GPU: NVIDIA GeForce RTX 5090, free VRAM 29,860 MiB at probe

Scheduled Task: \Codex\GPU Workload Scheduler
  state: Running
  LastTaskResult: 267009 (the expected running-task state, not a completed 0)
```

No queued or managed GPU task was cancelled or bypassed for this repair.
Long-duration watcher endurance, long-duration load endurance, and a real
Windows suspend/resume cycle remain excluded as previously documented.

## 2026-07-25 follow-up: CPU embedding timeout containment and clean migration

### Observed cause

The CPU-only Ollama embedding service is intentionally constrained to 0.5 CPU.
This is a thermal safety limit, not an inference-capacity setting. In a live
probe, a project-journal embedding request of about 228 tokens ran for 89
seconds and was cancelled by the 90-second request budget. The old worker then
retried similar work, producing `ReadTimeout` failures. This is evidence of an
insufficient CPU runtime, not evidence that the knowledge model is too small,
that a MoE model is defective, or that source data is corrupt.

Windows CIM did not expose an ACPI CPU temperature sensor on this machine, so
no unverified temperature was recorded. Container measurements after
containment showed the worker at 0.00–1.68% and Ollama at 0.00%; the previous
active inference sample was bounded to the configured half CPU.

### Implemented containment

- Added source-root `semantic_exclude_patterns`. The active WSL repository
  root now marks `**/docs/evidence/**` lexical-only. It remains discoverable by
  path/keyword search with document versions, chunks, and provenance intact.
- Added `purpose-aware-v3`, a deterministic 2,400-character embedding input
  bound, timeout batch splitting, and a non-destructive semantic-policy
  migration. The migration removed six obsolete derived vectors from 42
  excluded evidence documents; it changed **0** source files, chunks, or
  project keys.
- Fixed the first-ingestion branch so it uses the same bounded derived input
  as reindexing. Original chunk text is never truncated.
- Added a one-hour durable timeout circuit. Three recent `ReadTimeout`s open
  the circuit; documents continue through version/chunk/lexical ingestion with
  `embedding_status: deferred_runtime`. Successful recovery clears the current
  error while retaining `error_details.recovered_error` for audit.
- API prewarm is now disabled by default. While the circuit is open,
  `semantic` and `hybrid` requests return the explicit mode
  `*-degraded-keyword-only` without calling Ollama. The dashboard exposes the
  circuit and recent timeout count.
- Added a clean-DB-safe 0009 migration: 0001's metadata-based bootstrap can
  already contain a current column, so 0009 now checks before adding it.

### Executed evidence

```text
uv run ruff check services tests/unit
  PASS
uv run pytest tests/unit -q
  PASS: 80 passed in 0.52s

Dedicated PostgreSQL database lkp_test_policy_<timestamp>
  First run: NOT COUNTED. Fresh bootstrap exposed duplicate-column handling in
  migration 0009; the test database was dropped in finally cleanup.
  After idempotent migration fix: PASS, 21 integration tests in 5.26s
  (one upstream Starlette deprecation warning).

docker compose ... up -d --build api worker web
  PASS: Next.js 16.2.11 production build and TypeScript completed.

Disposable Playwright container (source mounted read-only)
  First install-only run: NOT COUNTED. pnpm required CI mode for a no-TTY
  dependency-directory replacement.
  CI=true rerun: PASS, 5/5 in 12.1s.

GET /api/v1/search/hybrid, circuit open
  PASS: mode=hybrid-degraded-keyword-only, 3 results, 58 ms;
  Ollama CPU observed at 0.00%.
```

### Live recovery result

```text
Before containment: pending 90, failed 144, dead-letter 23.
After circuit recovery: pending 0, processing 0, failed 0, dead-letter 23.
Dead-letter history: retained. All 23 were prior ReadTimeout rows; 18 unique
paths were retried as new jobs and completed under the circuit.

Current schema revision: 0009_source_root_semantic_policy
Embedding revision: ollama-qwen3-embedding-0.6b-ac6da0df-d1024-v1
Embedding model digest prefix: ac6da0dfba84
```

### Current truthful operational verdict

The portal is **VERIFIED for safe degraded operation**: read-only discovery,
document/version/provenance storage, lexical/path/symbol retrieval, project
journals, queue recovery, API/web health, and GPU scheduler monitoring are
working without CPU retry churn. The scheduled task
`\Codex\GPU Workload Scheduler` is Running and its health endpoint returned
`ok=true`; an unrelated managed GPU workload was active during the final
probe and was not touched.

Production semantic retrieval is **intentionally DEFERRED, not verified**.
The scheduler-aware GPU embedding batch/reindex runner is the remaining
implementation needed before semantic coverage can be claimed healthy. Do not
raise the CPU quota or bind the CPU embedding service to `gpus: all` as a
shortcut; both would bypass the measured thermal/scheduling decision recorded
in ADR 0016.

## 2026-07-25 follow-up: scheduled GPU recovery and safe journal display

### GPU recovery runner

Implemented a one-shot `embedding-reindex` Compose profile and the host wrapper
`scripts/reindex-embeddings.ps1`. The wrapper submits the WSL runner only through
`gpuq`; the temporary GPU Ollama service is private to Compose and is stopped on
success, failure, or cancellation. It uses the private `postgres` hostname rather
than the Windows host-only database endpoint.

Two implementation defects were observed and corrected rather than ignored:

- The first command let Compose replace the service command with `--limit`.
  The runner now explicitly invokes `python -m lkp_indexer.embedding_reindex`.
- The next attempt inherited the Windows tool database endpoint. The one-shot
  service now explicitly keeps `LKP_DATABASE_URL` on the Compose network.

A third run reached a healthy temporary GPU Ollama service. Its measured peak
was about 6.2 GiB above the concurrent baseline, proving that the original
2 GiB reservation was unsafe. That portal-owned job was cancelled through
`gpuq cancel`, not by killing a container or touching any other workload. The
wrapper now has an 8 GiB minimum/default reservation and rejects the old 2 GiB
value. A corrected 8 GiB, priority-30 job is queued behind existing managed GPU
work; queued is the expected safe state when its reservation cannot yet be
admitted. Completion is not claimed in this report while that job is queued.

### Journal display and redaction

During API verification, a historical journal presentation could contain a
credential-shaped value from an older activity. The raw activity ledger was
not deleted. Instead, a shared deterministic redactor now applies to hook
spooling, transcript capture, project-journal presentation, API activity,
evidence, candidate, case, and journal serialization. Legacy presentation
metadata is retained only for database audit and is not returned by the journal
API. The journal source hash now includes visible text so a presentation refresh
cannot leave an old managed Markdown page unchanged.

Executed evidence:

```text
uv run ruff check services tests/unit scripts
  PASS
uv run pytest tests/unit -q
  PASS: 83 passed in 0.51s
PowerShell parser, scripts/reindex-embeddings.ps1
  PASS
docker compose ... --profile manual-embedding config --quiet
  PASS
docker compose ... up -d --build api worker
  PASS
python -m lkp_indexer.cli refresh-journal-presentation
  PASS: changed 15 entries across 5 projects
GET /health/ready
  PASS: schema 0009_source_root_semantic_policy
Disposable Docker-network test database and read-only source mount
  PASS: 21 integration tests in 4.45s (one upstream Starlette deprecation warning)
```

Integration setup attempts that are **not counted as passes**: the operational
`.env` is Compose data rather than a POSIX shell script; the production
PostgreSQL port is intentionally not published to the WSL host; and an
editable Python build cannot write metadata into a read-only source mount.
The successful command therefore copied the read-only source into the
disposable container's `/tmp`, used the private Docker `postgres` network, and
dropped its newly-created test database in an exit trap.

Post-refresh API and managed-vault scans used pattern counters only; no secret
value was printed. Results: 34 journal entries, 0 unredacted journal patterns,
0 unredacted activity string fields, 0 unredacted generated-journal patterns,
and 34 journal titles containing valid Hangul code points. The earlier Korean
garbling in Windows PowerShell output was a console decoding artifact, not
stored/API UTF-8 corruption.

### Current truthful operational verdict

The portal remains **VERIFIED for safe degraded operation and safe human-facing
journal display**. GPU backfill, semantic query embedding, and normal semantic
or hybrid retrieval remain **not yet verified** until the correctly reserved
scheduled job finishes and their provenance results are measured. The explicit
keyword-only degradation remains the active safe behavior in the meantime.

### Cancellation cleanup hardening

The under-reserved run was cancelled through the scheduler, but its detached
temporary GPU Ollama container remained running. The portal-owned container was
then stopped gracefully with `docker compose ... stop ollama-embedding-batch`.
This exposed a cancellation path where WSL can terminate before the runner's
shell trap executes.

Prevention now has two independent safeguards: the admitted WSL runner stops a
stale batch before it starts, and the installed
`\LocalKnowledgePortal\ReapGpuEmbeddingBatch` task checks scheduler health and
the exact portal workload every 60 seconds. It only stops the named temporary
container when no portal reindex is active; scheduler failure is fail-closed.
The task was registered and observed `Running` with a snapshot showing
`state=no_batch_container`, `scheduler_ok=true`, and no active portal reindex.
No other GPU workload or container was stopped.

### Final validation rerun

```text
uv run ruff check services tests/unit scripts
  PASS
uv run pytest tests/unit -q
  PASS: 84 passed in 0.50s
Disposable Docker-network PostgreSQL test database
  PASS: Alembic 0001 through 0009, then 21 integration tests in 4.34s
  (one upstream Starlette deprecation warning)
docker compose ... build web
  PASS: production Next.js build
Disposable Playwright 1.61.1 container against the running portal
  First run: NOT COUNTED. A case-detail serializer treated structured
  `evidence_summary` as a string, producing HTTP 500 and one failed E2E.
  Fix: recursive display redaction for that JSON field.
  Rerun: PASS, 5/5 in 10.6s.
```

The final API checks returned `200` for a verified knowledge-case detail and
found zero unredacted credential-shaped patterns in a managed document, its
versions, keyword search results, and RAG context. API readiness remained
`ready`; web HTTP status remained `200`.

## 2026-07-25 follow-up: host GPU guard visibility and Korean knowledge UI

The portal now reads the bounded, atomic host reaper snapshot at
`E:\Data\LocalKnowledgePortal\runtime\gpu-embedding-reaper.json`. It does not
call the scheduler or Docker to infer this state. A missing, malformed, stale,
or scheduler-unconfirmed snapshot is displayed as offline/error/stale; only a
fresh, scheduler-confirmed `no_batch_container` or active scheduled batch is
healthy. This makes the cleanup safeguard observable in the right-hand WSL ·
Docker status panel without exposing any scheduler mutation token.

The Korean project-knowledge screen was visually checked after deployment. It
uses the portal typography rules (Pretendard/Noto Sans KR fallbacks for reading,
JetBrains Mono/Cascadia/D2Coding fallbacks for technical values), project → work
type filters, newest-first verified cases, and Markdown-oriented journal detail.
The earlier vague label `로컬 지식 편집기` is now `근거 기반 지식 선별기`; this
describes that the local model may organize evidence but does not itself make
facts true.

Executed evidence:

```text
uv run ruff check services tests/unit scripts
  PASS
uv run pytest tests/unit -q
  PASS: 86 passed in 0.48s
docker compose ... build api web
  PASS: FastAPI image build; Next.js 16.2.11 optimized production build,
  TypeScript, and static routes completed.
docker compose ... up -d --no-deps api web
  PASS: API recreated healthy, then web recreated.
GET /health/ready
  PASS: ready, schema 0009_source_root_semantic_policy
GET /api/v1/system/services
  PASS: one gpu-embedding-reaper row, healthy,
  "no temporary GPU embedding batch is running"
GET http://127.0.0.1:3010/
  PASS: HTTP 200
In-app visual check
  PASS: Korean `근거 기반 지식 선별기` and
  `GPU 임베딩 정리 보호장치` were present in the deployed DOM.
```

The direct WSL `pnpm --filter @lkp/web build` command was **not counted**:
the WSL shell lacks a standalone `pnpm` executable. The repository's pinned
Docker build uses Corepack and completed the same production Next.js command
with the lockfile. No lockfile or package version was changed to work around
that workstation tool-path issue.

Restart-safety probe at the same checkpoint:

```text
\LocalKnowledgePortal\StartAtLogon             Ready, LastTaskResult=0
\LocalKnowledgePortal\CollectDockerHealth      Running (persistent task)
\LocalKnowledgePortal\ReapGpuEmbeddingBatch    Running (persistent task)
\Codex\GPU Workload Scheduler                  Running (persistent task)
GET http://127.0.0.1:8790/api/health            ok=true
```

At the probe, one unrelated GPU workload was active and two jobs were queued.
The portal's correctly-reserved 8 GiB embedding reindex job remained queued;
it was not reordered, cancelled, or run outside `gpuq`. Therefore this update
does **not** change the outstanding semantic/hybrid verification verdict.

## 2026-07-25 follow-up: historical generic-candidate quarantine

The current ingestion path already sends a verified but generic completion
report to activity history and, when material, a project development journal;
it does not create a reusable-knowledge candidate without an explicit
goal/cause/approach/verification structure. A production audit found legacy
candidate rows created before that rule. Their common shape was an ordinary
implementation completion with a file list and successful command, but no
reusable cause or implementation decision.

The cleanup is intentionally non-destructive: it changes only the *derived*
candidate status to `activity_only`, stores a reversible quarantine reason and
timestamp in candidate metadata, and retains every ActivityEvent,
EvidenceRecord, document version, and project-journal entry. `review_only`
candidate APIs and the human review screen exclude this status. No source file,
managed Vault document, evidence row, or case was deleted or moved in this run.

Executed evidence:

```text
Disposable Docker-network PostgreSQL test database, copied source
  PASS: 21 integration tests in 5.45s (one upstream Starlette deprecation warning)
  Includes legacy generic auto-case retraction without deleting its audit trail.
docker compose ... exec worker python -m lkp_indexer.knowledge_quality
  DRY RUN: generic_candidate_quarantine candidate_count=38
docker compose ... exec worker python -m lkp_indexer.knowledge_quality --apply
  PASS: generic_candidate_quarantine candidate_count=38
GET /api/v1/knowledge/candidates?review_only=true
  PASS: total=0
GET /api/v1/project-journal
  PASS: total=34
GET /api/v1/knowledge/candidates (all statuses)
  PASS: total=42; historical audit rows retained, not deleted
Disposable Playwright 1.61.1 container
  First run: NOT COUNTED. An E2E expectation incorrectly required a
  candidate-only status message after the candidate list had correctly become
  empty. The product showed the intended empty state.
  Corrected expectation and rerun: PASS, 5/5 in 26.2s.
Docker web build and deployment
  PASS: Next.js 16.2.11 production build/TypeScript; web HTTP 200;
  API ready at schema 0009_source_root_semantic_policy.
```

The Korean screen was visually inspected after quarantine: the review tab had
zero list rows and the explicit empty state; verified cases and project journal
remain separate tabs. The qualification label is localized as `통과` rather
than a mixed-language `PASS` value. The current generation model remains
`qwen3.5:9b-q4_K_M` with a stored qualification `PASS`; the observed quality
problem was therefore not attributed to parameter count or MoE routing. Its
root cause was legacy candidate admission from structure-free completion
reports, now prevented for new activity and quarantined for historical rows.

## 2026-07-25 follow-up: restart-safe CPU semantic interlock

The previous timeout circuit opened only while its one-hour timeout window
contained enough events. When that window elapsed, the API could attempt CPU
embedding again even though no GPU backfill had completed. That is a recurrence
risk, not recovery evidence.

`LKP_EMBEDDING_RUNTIME_MODE=deferred_gpu_recovery` is now the production
interlock. It is passed to API and worker containers, survives restart, leaves
lexical indexing online, and forces semantic/hybrid requests to their explicit
keyword-only degradation path. The gpuq-admitted one-shot reindex keeps its
isolated bypass and is the only component allowed to repair the deferred
vectors. The operational environment file was changed only by adding this
non-secret setting; no credential value was read or reported.

Executed evidence:

```text
uv run ruff check services tests/unit tests/integration scripts
  PASS
uv run pytest tests/unit/test_semantic_embedding_bounds.py \
  tests/unit/test_service_catalog.py tests/unit/test_gpu_queue_proxy.py -q
  PASS: 13 passed in 0.42s
docker compose ... build api worker web
  PASS: FastAPI images and Next.js 16.2.11 production build/TypeScript.
docker compose ... up -d --no-deps api worker web
  PASS: API healthy, worker and web recreated.
GET /api/v1/metrics/summary
  PASS: embedding_runtime.open=true,
  mode=deferred_gpu_recovery, reason=gpu_recovery_pending.
POST /api/v1/search/hybrid (mode=semantic, query=PostgreSQL)
  PASS: semantic-degraded-keyword-only, 3 results.
POST /api/v1/search/hybrid (mode=hybrid, query=PostgreSQL)
  PASS: hybrid-degraded-keyword-only, 3 results.
```

These are degradation-path checks, not semantic-success claims. The reindex
job is still scheduler-queued, so production semantic coverage remains 1.11%
and actual vector/hybrid provenance verification remains outstanding.

Disposable Playwright 1.61.1 against the deployed localhost portal was rerun
after this interlock deployment: **PASS, 5/5 in 10.0s**. The search screen's
semantic and hybrid modes remained usable through the explicit safe-degradation
response; no test treated that as vector retrieval success.

Final isolated integration rerun: **PASS, 21/21 in 5.26s** (one upstream
Starlette `TestClient` deprecation warning). The first attempt is not counted:
the production `deferred_gpu_recovery` setting leaked into a deterministic
vector-persistence fixture. The fixture now explicitly uses
`embedding_runtime_mode="enabled"`, so it tests deterministic embedding
persistence rather than inheriting the operator's production thermal interlock.
The disposable test database was created with an `lkp_test_` name and dropped
in its exit trap.

## 2026-07-25 follow-up: curation queue deduplication repair

The host scheduler showed two queued `local-knowledge-portal-curation` jobs.
The scheduled task ran an older copied `C:\Docker\local-knowledge-portal\curate.ps1`,
then a reproduction exposed two PowerShell bugs in the source script: the
array concatenation/pipeline precedence was ambiguous, and a one-item filter
result was unrolled before the `.Count` check. The corrected script moves the
combination into a small imported helper and forces the caller result to an
array before checking it.

The installer made a timestamped backup of the differing operational script
before idempotently copying the corrected source and re-registering the same
60-minute task. Four exact, confirmed portal-owned duplicate curation job IDs
created by the old/reproduction paths were cancelled via `gpuq cancel`; no
other workload, container, or source data was touched. One oldest curation
snapshot remains queued by design, alongside the independently queued
embedding reindex.

Executed evidence:

```text
PowerShell parser, C:\Docker\local-knowledge-portal\curate.ps1
  PASS
Pure PowerShell queue helper regression test
  PASS: one queued=1, active+queued=2, no match=0.
Run corrected operational curate.ps1 with exactly one queued curation job
  PASS: exit=0; logged "already queued or active"; before=1, after=1.
GET GPU scheduler status
  PASS: only one queued curation workload remains; unrelated voice workload
  remains the sole active GPU reservation.
```

## 2026-07-25 follow-up: visible semantic-recovery status

The prior dashboard exposed that semantic retrieval was deferred, but did not
show whether a recovery was actually admitted, how much GPU memory it required,
or why it was still waiting. The read-only API now derives a narrow recovery
view from the existing host scheduler status: it selects this portal's active,
then oldest queued, embedding-reindex job and returns only safe scheduling
fields. It intentionally omits the scheduler's command argv, environment, and
all mutation capabilities. The Korean overview renders the resulting state as
`GPU 재색인 대기`, including the reservation state, requested VRAM, and the
scheduler's own decision text.

Executed evidence:

```text
GET http://127.0.0.1:8010/health/ready
  PASS: ready=true
GET http://127.0.0.1:8010/api/v1/embedding/recovery
  PASS: runtime=deferred_gpu_recovery, reason=gpu_recovery_pending,
  reindex=queued, requested_vram_mb=8192,
  scheduler_decision=head-blocks-backfill:8192>6213.
GET http://127.0.0.1:3010/
  PASS: HTTP 200
docker compose ... ps api web
  PASS: API running/healthy; web running.
docker stats --no-stream (portal API/worker/watcher/web)
  PASS snapshot: 0.12% / 0.26% / 0.58% / 0.00% CPU respectively.
  This is an instantaneous post-deployment observation, not an endurance claim.
uv run ruff check services tests/unit tests/integration scripts
  PASS
uv run pytest tests/unit
  PASS: 88 passed in 0.49s.
corepack pnpm --dir apps/web test
  PASS: Playwright 1.61.1, 6/6 in 10.8s.
  Includes a deterministic mocked Korean recovery-banner scenario and the
  existing live-host GPU-queue screen scenario.
```

An initial direct Playwright file argument from the web package reported
`No tests found`; that was an invocation-path error and is **not counted**.
The configured package test command above is the successful result. This
checkpoint does not claim a completed semantic backfill: the scheduler still
has the portal 8 GiB job queued behind an unrelated active reservation. The
CPU interlock remains active, and keyword retrieval remains available while
that safe recovery path waits.

## 2026-07-25 follow-up: reproducible isolated integration suite

Running `pytest tests/integration` without `LKP_TEST_DATABASE_URL` correctly
skips all integration tests; that result is not integration verification. To
make the real check repeatable without exposing or targeting production data,
`scripts/run-isolated-integration-tests.sh` now creates a unique
`lkp_test_verify_*` PostgreSQL database, starts a one-off private-network API
test container with the repository mounted read-only, and invokes the existing
test helper. Its cleanup trap can drop only the exact database that its own
successful `createdb` call created; a generated-name collision fails before
that flag is set.

Executed evidence:

```text
sh scripts/run-isolated-integration-tests.sh
  PASS: 21 passed in 5.41s
  One upstream Starlette TestClient deprecation warning.
  Dedicated database lkp_test_verify_20260725051817_34659 was reported as
  dropped by the runner's exit trap.
corepack pnpm --dir apps/web lint
  PASS: tsc --noEmit
```

The earlier direct `pytest tests/integration -q` run had no dedicated database
URL and therefore produced `21 skipped`; it is deliberately excluded from the
pass count. This runner is documented in the README for future repeatable
verification.

## 2026-07-25 follow-up: GPU-only semantic recovery proof chain

The CPU interlock makes it unsafe to call the ordinary API semantic path merely
to declare a recovery complete. A separate one-shot probe now starts the same
temporary GPU Ollama endpoint through `gpuq`, selects a current already-indexed
production chunk without writing its text, and performs both semantic and
hybrid retrieval. It fails closed unless both results contain vector matches
with complete provenance. Its atomic runtime snapshot contains only state,
timestamps, revision, counts, similarity, and provenance-completeness flags.

`ValidateSemanticRecovery` is a restart-safe ten-minute task. It exits while
the reindex is queued/running, requires the newest completed reindex to be
successful, and suppresses repeat submissions when its proof snapshot is newer
than that reindex. This prevents a stale vector sample from being reported as a
successful recovery and prevents periodic duplicate GPU jobs.

Executed evidence:

```text
uv run ruff check services tests/unit tests/integration scripts
  PASS
uv run pytest tests/unit
  PASS: 91 passed in 0.49s.
PowerShell parser, validation and installer scripts
  PASS
powershell ... validate-semantic-recovery.ps1 while reindex is running
  PASS: exited 0 with "waits for reindex"; no validation GPU job submitted.
powershell ... install-semantic-recovery-validation-schedule.ps1 -IntervalMinutes 10
  PASS: \LocalKnowledgePortal\ValidateSemanticRecovery registered Ready/enabled.
GET GPU scheduler status after registration
  PASS: one active portal reindex; zero validation jobs.
docker compose ... build api web && up -d --no-deps api web
  PASS: FastAPI and Next.js production builds; API recreated healthy and web
  recreated. The independent active reindex container was not restarted.
```

Live progress is intentionally not counted as semantic retrieval proof. At the
latest sample the scheduler-owned reindex was `running`, semantic chunks had
grown from 339 to 537, and the follow-up proof snapshot was absent as expected.
The task must still complete its GPU probe successfully before the portal can
claim vector semantic/hybrid recovery.

## 2026-07-25 follow-up: visible GPU recovery backlog

The recovery view now counts the remaining current production documents and
unembedded chunks for the active embedding revision. It does not infer a
percentage from a stale initial scan, and it does not expose any chunk text.
The Korean banner shows reservation state, requested VRAM, scheduler decision,
GPU proof state, and the current vector backlog together so an operator can
distinguish active recovery from a merely queued job.

Executed evidence:

```text
GET /api/v1/embedding/recovery after deployment
  PASS: reindex=running; progress pending_documents=116,
  pending_chunks=1438; validation=null before the post-reindex probe.
GET /health/ready and GET http://127.0.0.1:3010/
  PASS: ready=true; web HTTP 200.
uv run ruff check services/api/lkp/main.py tests/unit/test_gpu_queue_proxy.py
  PASS
uv run pytest tests/unit/test_gpu_queue_proxy.py tests/unit/test_embedding_recovery_probe.py
  PASS: 8 passed in 0.42s.
corepack pnpm --dir apps/web lint
  PASS: tsc --noEmit.
docker compose ... build api web && up -d --no-deps api web
  PASS: both production builds; API healthy and web started without restarting
  the independent GPU reindex container.
corepack pnpm --dir apps/web test
  PASS: Playwright 1.61.1, 7/7 in 12.5s.
```

The first E2E attempt after adding the progress assertion is **not counted**:
the assertion selected a hidden hydration duplicate of the same text. The test
now asserts the visible `.semantic-recovery` container's contents, and the
rerun above is the counted result.

## 2026-07-25 follow-up: scheduler-unavailable truthfulness

The recovery banner previously treated a failed read-only scheduler request as
an absent reservation. That was not an acceptable operational statement: an
operator could distinguish neither “not submitted” from “scheduler cannot be
read.” The overview now renders an explicit Korean/English unavailable state
and does not reuse the `예약 없음` / `Not submitted` label for that failure.
The API remains read-only; no scheduler token, command argv, or POST action is
introduced.

Executed evidence:

```text
corepack pnpm --dir apps/web lint
  PASS: tsc --noEmit
docker compose ... build web
  PASS: Next.js 16.2.11 optimized production build and TypeScript.
docker compose ... up -d --no-deps web
  PASS: web container recreated and started; HTTP 200 after readiness wait.
corepack pnpm --dir apps/web test
  PASS: Playwright 1.61.1, 7/7 in 12.2s.
  Includes a deterministic 503 scheduler scenario asserting Korean
  `스케줄러 연결 확인 필요`, and asserting that `예약 없음` is absent.
```

The first E2E launch immediately after `up -d` is **not counted**: the new web
container had not yet accepted HTTP requests, so Playwright attempted its
fallback local `pnpm dev` command, which is intentionally unavailable in WSL.
The rerun waited for deployed web HTTP 200 and is the counted result.

## 2026-07-25 follow-up: evidence-first journals, bounded dedup, and queue timing

### Implemented

- Codex's global `C:\Users\kutae\.codex\AGENTS.md` now gives a **non-mandatory**
  final-answer convention for a one/two-line `재사용 메모`.  It is emitted only
  when a worker has direct execution or independently checked evidence that the
  point is likely to recur.  It carries situation, cause, action, and
  verification when available.  Routine work and unverified assertions omit it.
  The collector treats this memo as candidate structure only; it is never
  execution evidence by itself.  The prior global instruction file was backed
  up without overwrite at
  `D:\LocalBackup\LocalKnowledgePortal\config\codex\2026-07-25T120448465\AGENTS.md`.
  The instruction applies to new Codex sessions.  Existing hook trust was not
  changed.
- The hook collector now recognizes structured `exit_code`, `exit_status`,
  `return_code`, and bounded `tool_output`/`tool_response` variants, then a
  bounded transcript fallback.  It stores the evidence source.  A final
  assistant report still cannot establish a pass or failure.
- Project journal pages now always render **Failures and resolution**.  Where
  no non-zero command exit was observed they state that explicitly rather than
  leaving a blank section or inventing a cause.  A presentation revision in the
  managed-page source hash forces safe regeneration when this wording changes.
  All six project indexes and 35 existing managed entry pages were regenerated
  under `_generated`; no human Vault files were changed.
- Knowledge candidates use exact content/cause/solution dedup first.  New
  non-exact candidates are held at `pending_gpu_vector_check`; deterministic
  Korean/English key terms narrow candidates before the GPU-only pgvector
  comparison.  High-similarity matches become `NEEDS_REVIEW`, while only a
  verified no-match may proceed to normal automatic publication.  Rebuildable
  comparison vectors live in the separate `knowledge_similarity_embedding`
  table and use the active provider/model/digest/dimension/revision.
- Jobs API and Operations table now expose exact timestamps and queue lead,
  processing duration, and age as integer milliseconds.  The time cells are
  localized and do not report a negative duration.
- Rolling is non-destructive: raw spool is removed only after successful import;
  terminal successful/cancelled job detail and routine ingest-event detail are
  marked `rolled_up` after 90 days and omitted by default; activity detail has
  its existing 30-day rollup.  Documents, versions, chunks, canonical cases,
  evidence, source records, dead letters, and backups are never auto-deleted.
  Physical removal requires an explicit retention procedure, backup, and user
  authorization.

### Production audit and current state

```text
PostToolUse with recorded exit code: 2,430
Recorded non-zero PostToolUse exits: 0
Project journal entries: 35; entries with observed command failure: 0
Historical ingest ReadTimeout records: 33 (latest 2026-07-24 16:49 UTC)
Current pending/leased/processing ingest jobs: 0
24h queue lead (created -> first started): p50 1,426,745 ms; p95 1,927,512 ms
Current durable data: 4,765 documents / 4,941 versions / 48,279 chunks /
9 canonical knowledge cases
Current rolled-up rows: activity 1; terminal jobs 0; ingest events 0
```

`ReadTimeout` is an upstream embedding HTTP timeout, not a queue-lead timeout.
The historical failures are retained as dead letters; production semantic
runtime remains `deferred_gpu_recovery`, so CPU fallback cannot repeat that
load.  The current queue is empty.  No historical journal has sufficient
non-zero-exit evidence to honestly fill an error cause/resolution; those pages
now state this fact and retain their reported result separately.

The new `\LocalKnowledgePortal\DeduplicateKnowledge` scheduled task is
registered every 30 minutes, uses `IgnoreNew`, a five-minute execution limit,
and only submits a low-priority `gpuq` workload when candidates are pending.
It was manually started once with no pending candidate and completed with
`LastTaskResult=0`; no GPU workload was submitted.  The dedup-status endpoint
reported `pending_candidates=0`, policy `key_terms_then_pgvector-v1`, and
runtime `deferred_gpu_recovery` at verification time.

### Executed evidence

```text
uv run pytest tests/unit/test_project_journal.py
  PASS: 2 passed in 0.30s
uv run ruff check services tests/unit tests/integration scripts
  PASS
uv run pytest tests/unit
  PASS: 96 passed in 0.53s
bash scripts/run-isolated-integration-tests.sh
  PASS: 23 passed in 5.78s; one upstream Starlette TestClient warning.
  Dedicated lkp_test_verify_20260725122147_75352 database dropped by exit trap.
corepack pnpm --dir apps/web lint
  PASS: TypeScript no-emit
corepack pnpm --dir apps/web build
  PASS: Next.js 16.2.11 production build (earlier in this same change set;
  no frontend source changed afterward).
./scripts/docker-stack.sh up
  PASS: application/web images rebuilt; migration container exited 0; API,
  worker, watcher, hook collector, and web were recreated.
GET /health/ready
  PASS: ready=true; schema_revision=0010_knowledge_dedup_vectors.
POST/GET operational API probes
  PASS: jobs expose millisecond fields; dedup status is read-only.
python -m lkp_indexer.project_journal (inside API container)
  PASS: six managed project indexes and all current entry pages materialized.
Start-ScheduledTask \LocalKnowledgePortal\DeduplicateKnowledge
  PASS: Ready, LastTaskResult=0; no pending candidate means no gpuq submission.
corepack pnpm --dir apps/web exec playwright test --workers=1
  PASS: 7/7 in 13.2s against deployed localhost portal.
```

The direct `./scripts/run-isolated-integration-tests.sh` attempt was refused by
its missing executable bit; the exact same script passed when invoked through
`bash`.  This is a shell invocation issue, not a test failure.  No semantic
recovery, vector-dedup GPU comparison, or long endurance run is claimed here:
there were no pending dedup candidates, and the GPU-only recovery interlock
remains correctly active.

## 2026-07-25 follow-up: curation scheduler entrypoint and no-work GPU guard

The `CurateKnowledge` scheduled task was found to point at
`curation-queue.psm1` rather than `curate.ps1`.  That module is an import-only
helper, so the task's latest result was non-zero and it could not submit the
intended curation workload.  The cause was the installer reusing the loop's
last `$target` value after copying the executable and module.  It now uses the
explicit `$curateTarget` path.

The curation API also exposes `eligible_candidates`, and both the host script
and curator query exclude published/activity-only/held candidates and
`pending_gpu_vector_check` records.  The schedule therefore does not reserve a
generation model merely to discover no usable work.  Candidates awaiting the
separate GPU vector-dedup pass remain excluded until that pass completes.

Executed evidence:

```text
PowerShell parser: curate.ps1, install-curation-schedule.ps1
  PASS
uv run ruff check services tests/unit tests/integration
  PASS
uv run pytest tests/unit
  PASS: 96 passed in 0.53s
bash scripts/run-isolated-integration-tests.sh
  PASS: 23 passed in 5.76s; disposable lkp_test_verify_20260725122926_77404
  database dropped by exit trap; one upstream Starlette warning.
./scripts/docker-stack.sh up
  PASS: rebuilt/recreated portal services; API ready.
GET /api/v1/knowledge/curation/status
  PASS: enabled=true, eligible_candidates=0, scheduler=idle.
install-curation-schedule.ps1 -IntervalMinutes 60
  PASS: task action now names C:\Docker\local-knowledge-portal\curate.ps1.
Start-ScheduledTask \LocalKnowledgePortal\CurateKnowledge
  PASS: LastTaskResult=0; scheduler reports zero portal curation GPU jobs.
corepack pnpm --dir apps/web exec playwright test --workers=1
  PASS: 7/7 in 13.2s against deployed localhost portal.
```

The manual-profile `knowledge-curator` container's historical `Exited (137)`
record is not a required daemon and is not used as the automatic scheduler.
The corrected Windows scheduled task is the restart-safe entrypoint.  It will
submit a GPU reservation only when a future eligible candidate exists.

## 2026-07-25 follow-up: GPU queue controls and external ComfyUI VRAM attribution

Implemented a server-side-only GPU queue control path. The portal now requests a
safe stop only for a scheduler-managed child job, and updates a complete queued
order atomically with `manual_rank`. Browser drag/drop and keyboard arrows send
the complete current queue; stale queue changes are rejected with HTTP 409.
Manual order does not bypass VRAM safety, the parallel-job limit, or safe
short-job backfill. `configure-gpu-queue-control.ps1` copies the existing host
scheduler token into the ignored portal environment with a timestamped backup;
the token was not printed or exposed to the browser.

The investigation found a separate visibility gap: ComfyUI was holding roughly
25 GiB in its long-lived server while the GPU queue had zero managed jobs. Its
prompt bridge was healthy and has historical successful `comfyui-prompt` queue
jobs, but server-warmed model memory is not a scheduler child and therefore
cannot honestly appear as one. The scheduler now has a bounded 15-second
NVIDIA process census and the portal labels global GPU use with no managed job
as external. Per-process VRAM remains `N/A` under the current Windows WDDM
driver, so no per-process value is fabricated. The running ComfyUI process was
not restarted because it was user-owned and had loaded model state; its new PID
health field takes effect on its next ordinary restart.

Executed evidence:

```text
gpu-workload-scheduler pytest
  PASS: 8 passed in 0.62s
Portal ruff + GPU queue proxy tests
  PASS: ruff clean; 8 passed in 0.44s
Portal API/web Docker build
  PASS: FastAPI image built; Next.js 16.2.11 production build passed.
Queue control end-to-end, using only task-owned test jobs
  PASS: a 64 MiB CPU-only managed child was started then cancelled through
        POST /api/v1/gpu-queue/jobs/{id}/cancel.
  PASS: two intentionally non-fitting 30,000 MiB jobs were reversed through
        POST /api/v1/gpu-queue/reorder; host status persisted the new order.
  PASS: both validation jobs were cancelled and ended `canceled`.
Host safe restart before the validation run
  PASS: performed only while active=0 and queued=0; process 24724 -> 42900;
        `manual_rank` schema migration present.
ComfyUI bridge inspection
  PASS: GET http://127.0.0.1:8188/gpuq_bridge/health returned ready=true,
        scheduler_url=http://127.0.0.1:8790, requested_vram_mb=26000.
  OBSERVED: its current process had external driver visibility but is not a
        queue-managed child. No stop/restart was issued.
Host scheduler process census deployment
  PASS: after the unrelated job completed, the scheduler was rechecked with
        active=0 and queued=0, then gracefully restarted (42900 -> 42188).
        Portal status reported `managed_running=0`, `gpu_used_mb=7231`,
        `unmanaged_gpu_process_count=25`, and no process scan error. The
        ComfyUI process itself was not restarted.
ComfyUI recurrence guard
  PASS: `run-comfyui-gpuq.ps1` was added for explicit server-managed mode.
        It refuses to replace an existing 127.0.0.1:8188 listener, submits a
        `comfyui-server` gpuq job only after that guard, and starts ComfyUI
        with nested prompt reservations disabled because the parent server
        reservation owns the model lifetime.
  PASS: PowerShell parser check and 9 CPU-only bridge unit tests passed.
  PASS: against the currently running direct ComfyUI listener, the new launcher
        refused without stopping or replacing the user process.
```

## 2026-07-25 follow-up: project journal candidate reassessment

The apparent lack of new verified knowledge cases was a policy-path problem,
not a missing-hook problem. At inspection time the portal had three canonical
cases, forty verified project-journal entries, and thirty-eight historical
automatic candidates marked `activity_only`. The previous deterministic gate
discarded a generic but verified operational/configuration change before the
local evidence-bound editor could assess it. This correctly suppressed routine
file-change noise, but was too broad for the documented project-journal policy.

The repaired path preserves the separation:

- simple presentation changes and generic completion reports remain activity
  history only;
- verified failure/fix work and explicit reusable memos retain their established
  knowledge path;
- a verified, otherwise-generic operational/configuration journal becomes an
  editorial candidate only, never an immediately published case;
- the local editor may publish only after its draft cites the observed change
  and execution evidence and passes deterministic validation. A non-publish
  editorial recommendation remains `needs_review` or `activity_only`.

The collector performs a one-time, reversible reassessment of matching legacy
candidate rows. It preserves immutable activities, evidence records, candidate
IDs, and prior quarantine metadata. Production recheck after deployment:

```text
GET /health/ready
  PASS
GET /api/v1/knowledge/curation/status
  verified_cases=3, project_journals=40,
  editorial_candidates=30, activity_only=8, eligible_candidates=30
  scheduler_state=idle
GPU scheduler before curation submission
  OBSERVED: unrelated wedding-v6-qwen-multiangle-pilot job active,
  requested_vram_mb=22900, GPU free=1029 MiB.
scripts/curate.ps1 -VramMiB 8192 -EstimatedSeconds 1800 -Priority 40
  PASS: submitted one gpuq-managed batch eda6407d-7872-49cd-9580-1a23ba5abb51.
  DEFERRED: model editorial processing will start only after the unrelated
  reservation releases enough GPU memory; no direct model launch was used.
Verification
  PASS: ruff clean; 19 unit tests passed in 0.43s.
  PASS: isolated disposable PostgreSQL integration suite: 24 passed in 6.61s;
        generated test DB was dropped by its exit trap.
  PASS: API/web Docker rebuild; Next.js 16.2.11 production TypeScript build
        passed. Only api, hook-collector, and web were recreated.
```

## 2026-07-25 correction: curation runtime and project-knowledge counts

The above initially submitted curation job `eda6407d-7872-49cd-9580-1a23ba5abb51`
must not be interpreted as editorial success. It exited `0`, but its command
used Compose `--no-deps`, so it did not start `ollama-generation` or verify the
configured generation model. The curator consequently recorded
`state=model_unavailable` and published no cases. This was an execution-path
bug, not a model-quality result.

`scripts/curate.ps1` now submits only `scripts/run-gpu-curation.sh` through
`gpuq`. The wrapper refuses a pre-existing generation runtime that could belong
to another reservation; otherwise it starts the runtime, waits up to 90 seconds
for health, verifies/pulls the configured model, runs the full frozen candidate
batch, and stops only the runtime it started. It does not expose a generation
endpoint or leave a model resident after the GPU reservation ends.

Executed evidence:

```text
bash -n scripts/run-gpu-curation.sh
  PASS
Pester curation launcher tests
  PASS: 2 passed, 0 failed
scripts/curate.ps1 -VramMiB 8192 -EstimatedSeconds 1800 -Priority 40
  PASS: submitted gpuq job 459e791e-fe88-4c99-91d0-1aaacd02f54f.
GPU scheduler job 459e791e-fe88-4c99-91d0-1aaacd02f54f
  PASS: started 2026-07-25T08:48:20Z, finished 2026-07-25T08:51:35Z,
        exit_code=0, peak_total_gpu_used_mb=12849.
Curator run
  PASS: qwen3.5:9b-q4_K_M digest
        6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7
        passed the deterministic qualification harness.
  PASS: processed_candidate_count=30, failed_candidate_count=0.
  OBSERVED: all 30 received NEEDS_REVIEW. The model withheld publication
        because the source evidence established changed files/test exits but
        did not establish a reusable cause/implementation decision. No
        unsupported canonical case was fabricated.
  OBSERVED: verified_cases=3, project_journals=41,
        editorial_candidates=31, activity_only=8.
Generation runtime cleanup
  PASS: ollama-generation was Exited (0) after the reservation; it was not
        retained as an unmanaged GPU consumer.
Project-knowledge presentation
  PASS: API/web production rebuild completed; Next.js 16.2.11 TypeScript
        production build passed.
  PASS: refresh-journal-presentation updated 41 managed project-journal
        records without changing source activities.
  PASS: browser check at http://127.0.0.1:3010 found non-empty content, no
        framework error overlay, and the Project Knowledge default tab set to
        Project Journal. It displayed: "검증 사례 3 · 개발 일지 41 · 편집 후보
        31 · 일반 활동 8".
  PASS: journal rendering labels reported work separately from observed changed
        files and execution verification; no reported claim is relabelled as
        independently verified evidence.
```

The resulting count is intentionally three canonical reusable cases, not three
project-work records. Project journals are the primary view for significant
work; canonical cases remain the narrower, evidence-cited reusable subset.

## 2026-07-25 final follow-up: document relations, local-model capture, and UX audit

This pass addressed the portal's user-facing information architecture as well
as two correctness issues found during visual inspection.

Implemented:

- Document relations now represent only explicit Obsidian `[[wikilinks]]` and
  relative Markdown links. The graph does not claim semantic similarity or
  inferred code imports. It requires a project scope, supports link-type and
  resolution filters, limits the first view to forty relationships, and
  explains resolved and unresolved links in the UI.
- A bounded read-only backfill extracted 671 relationships from 1,872
  Markdown documents; 216 resolved to indexed documents. No source file was
  modified.
- Local-model conversations have a separate atomic spool and activity source.
  The callback endpoint is `POST /api/v1/local-llm/hooks/chat`; the collector,
  not the hook, owns database ingestion. Captured conversations remain
  unverified activity until independent execution evidence exists.
- The default context panel is collapsed so the primary reading area receives
  the available width. Search-result selection opens it on demand.
- Internal scheduler decisions, job states, error class names, hook event
  names, worker modes, schema revisions, and GPU workload keys are translated
  to user-facing Korean/English labels. Raw identifiers, commands, paths, and
  logs are retained behind an explicit technical-details disclosure or a
  tooltip where operational troubleshooting needs them.
- GPU queue details open inline below the selected row through a keyboard-
  accessible button. The stop action remains confirmation-gated and scoped to
  the scheduler-owned process. Queue ordering and pagination remain intact.
- The project-knowledge summary was reduced to tab badges; the page no longer
  repeats four aggregate counts as a separate sentence.
- The operations table now preserves readable column widths with horizontal
  overflow instead of collapsing Korean labels one character per line.
- The portal service aggregation now treats the optional generation editor's
  `idle` and `busy` states as healthy operation. Previously all child services
  could be healthy while the portal group incorrectly displayed `error`.

Executed evidence:

```text
uv run ruff check .
  PASS: All checks passed.
uv run pytest tests/unit -q
  PASS: 106 passed in 0.60s.
bash scripts/run-isolated-integration-tests.sh
  PASS: 29 passed in 6.29s; disposable PostgreSQL database removed by exit trap.
pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build and TypeScript checks passed.
docker compose ... build api web
  PASS: application and web images built successfully.
docker compose ... up -d --no-deps api web worker watcher hook-collector
  PASS: services recreated from the verified images.
GET /health/ready
  PASS: ready; PostgreSQL 18.4; schema revision 0012_block_semantic_dedup;
        Ollama embedding reachable.
GET /api/v1/graph/facets
  PASS: 12 project facets returned.
GET /api/v1/system/services
  PASS: overall=healthy; local-knowledge-portal group=healthy.
GET http://127.0.0.1:3010/
  PASS: HTTP 200.
```

Browser verification used the in-app browser at a 1280-pixel desktop viewport.
Final screenshots are stored under
`C:\Users\kutae\Documents\Codex\2026-07-23\blast-radius-1-local-knowledge-base\ui-audit-2026-07-25\final`.
The overview, source explorer, project knowledge, activity history, operations,
document-relations, GPU queue, and inline GPU detail states were inspected.

Current verified production facts:

```text
document_link total=671
document_link resolved=216
graph project facets=12
system services overall=healthy
portal service group=healthy
web HTTP=200
```

Remaining manual/explicit boundaries:

- Ollama itself does not expose a universal post-response hook. Each local chat
  client must call the provided callback adapter after a completed turn.
- Codex hook trust remains a user-controlled review boundary; installers do not
  bypass it.
- Long-duration watcher endurance, sustained load, and real Windows
  suspend/resume endurance remain excluded from this bounded verification.

## 2026-07-25 project journal consolidation

Project development journals now have two explicit lifecycle views:

- **Current document:** exactly one rolling `development-journal.md` per
  project. Its sections are numbered newest-first and each number resolves to
  a source record containing the immutable activity identifier and execution
  evidence count.
- **Work history:** the prior one-entry-per-work records remain append-only in
  PostgreSQL and under each managed `Journal/` directory. They were not deleted
  or rewritten as source data.

The default Project Knowledge view lists projects, not individual work
records. The `작업 이력` tab exposes the original paginated history. The current
article includes a table of contents, numbered work sections, verification
counts, changed-file counts, and a final source list. The API preserves the
history endpoints and adds:

```text
GET /api/v1/project-journal/projects
GET /api/v1/project-journal/projects/{project}
```

Executed evidence:

```text
uv run ruff check services/api/lkp/main.py services/indexer/lkp_indexer/project_journal.py tests/unit/test_project_journal.py
  PASS: All checks passed.
uv run pytest tests/unit -q
  PASS: 107 passed in 0.56s.
bash scripts/run-isolated-integration-tests.sh
  PASS: 29 passed in 6.24s; disposable PostgreSQL database removed by exit trap.
pnpm --filter @lkp/web lint
  PASS: TypeScript no-emit check passed.
pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build passed.
docker compose ... build api web
  PASS: application and web images built.
docker compose ... up -d --no-deps api web worker watcher hook-collector
  PASS: verified images started and API readiness became healthy.
python -m lkp_indexer.cli refresh-journal-presentation
  PASS: 47 entries refreshed across 8 projects.
GET /api/v1/project-journal/projects?page=1&page_size=100
  PASS: 8 current project documents representing 47 retained history entries.
GET /api/v1/project-journal/projects/local-knowledge-portal
  PASS: 11 numbered entries; citation 1 includes its source activity identifier.
managed development-journal.md inspection
  PASS: headings 1 through 11 and a final `## 출처` section are present.
managed Journal entry inspection
  PASS: the individual history page remains a standalone unnumbered entry.
```

In-app browser verification at the 1280-pixel desktop viewport confirmed:

- the default `통합 문서` tab shows eight project rows;
- the selected project renders a single numbered wiki-style article and source
  list;
- `작업 이력` switches to the retained 47-entry, two-page history;
- the Korean labels and keyboard-operable tabs are exposed through semantic
  tab and tabpanel roles.

### Current-document correction

The first consolidation still rendered every history entry as a numbered body
section. That did not meet the intended wiki/current-state model and was
replaced rather than treated as complete.

The corrected implementation:

- selects only the newest source record for each current-state topic
  (`implementation`, `operations`, `performance`, `error_resolution`);
- renders those topic snapshots as one current project document;
- keeps all older records exclusively in the paginated `작업 이력` view and
  immutable managed `Journal/` pages;
- exposes source numbers as compact buttons beside each current section;
- opens the original intent, work report, timestamp, changed-file count, and
  exit-code-backed verification directly below the clicked citation;
- replaces the three-column tree/list/detail layout in current-document mode
  with a horizontal project selector and a full-width article;
- removes the 1440-pixel cap from portal content so the main area uses all
  available width while the context panel is collapsed.

Executed evidence:

```text
uv run ruff check services/api/lkp/main.py services/indexer/lkp_indexer/project_journal.py tests/unit/test_project_journal.py
  PASS: All checks passed.
uv run pytest tests/unit -q
  PASS: 107 passed in 0.57s.
bash scripts/run-isolated-integration-tests.sh
  PASS: 29 passed in 6.50s; disposable database removed by exit trap.
pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build and strict TypeScript checks passed.
docker compose ... build api web
  PASS: application and web images built.
python -m lkp_indexer.cli refresh-journal-presentation
  PASS: 48 retained entries refreshed across 8 projects.
GET /api/v1/project-journal/projects/local-knowledge-portal
  PASS: 12 retained history entries represented by 3 current topic sections
        and 3 cited source records; no concatenated `entries` payload.
managed development-journal.md inspection
  PASS: `현재 구현`, `운영과 설정`, `성능과 안정성`, and `근거` sections;
        old per-work sections are absent from the current document.
GET /health/ready and GET http://127.0.0.1:3010/
  PASS: HTTP 200.
in-app browser interaction
  PASS: full-width current document, automatic latest-project selection,
        compact citation button, source panel, original report, and execution
        evidence were all visible and operable.
```

### GPU runtime attribution and safe-stop hotfix

The host scheduler now distinguishes scheduler-owned jobs, allowlisted Ollama
runtimes, and observed unmanaged GPU processes. Portal controls never accept a
PID, container name, model name, or command from the browser.

Implemented:

- registered the portal embedding and generation Ollama containers in the
  scheduler's operator-owned allowlist;
- exposed loaded model, digest prefix, size, CPU/GPU processor, context, and
  keep-alive state through the read-only status response;
- added an authenticated server-side unload endpoint that operates only on
  model names discovered with `ollama ps` in the exact configured container;
- added the Korean/English `Ollama 모델 상태` panel and a confirmation-gated
  model unload control;
- moved the managed-job safe-stop control into every running job row while
  retaining the expandable detail control;
- changed the standard ComfyUI launcher to submit through
  `run-comfyui-gpuq.ps1`; `-ServerManaged` is the only direct launch path, which
  prevents recursion and keeps future ComfyUI sessions scheduler-owned.

Hotfix execution on 2026-07-26:

```text
nvidia-smi + CIM + listener inspection
  PASS: PID 49608 was E:\AI\Apps\ComfyUI\main.py, child of the ComfyUI venv
        launcher PID 49984, listening only on 127.0.0.1:8188.
GET http://127.0.0.1:8188/queue
  PASS: 0 running and 0 pending ComfyUI prompts before termination.
POST /interrupt (only when needed) + POST /free
  PASS: model unload/free-memory path completed.
validated Stop-Process for PID 49608/49984
  PASS: both exact command-line-validated ComfyUI processes exited and port
        8188 stopped listening.
post-stop scheduler status
  PASS: the scheduler queue was not cancelled or rewritten; a previously
        queued non-ComfyUI job was allowed to start normally.
PowerShell AST parse of run-comfyui.ps1 and run-comfyui-gpuq.ps1
  PASS: both scripts parsed successfully.
pnpm --filter @lkp/web lint
  PASS: strict TypeScript no-emit check passed.
pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build passed.
docker compose ... build web && docker compose ... up -d --no-deps web
  PASS: web image rebuilt; /gpu-queue returned HTTP 200.
pnpm exec playwright test -g "GPU queue"
  PASS: Ollama status, active-row safe stop, expandable details, and completed
        job details passed (1 test).
```

Current attribution after the hotfix:

- ComfyUI: stopped; no `main.py` process and no listener on port 8188;
- portal embedding Ollama: `qwen3-embedding:0.6b`, 2.4 GB model, 100% CPU;
- portal generation Ollama: stopped;
- subsequent GPU usage belongs to scheduler-managed work and was intentionally
  not cancelled.

One full Playwright-suite run during this change reported five stale assertions
from earlier UI changes (hidden responsive service heading, intentionally
humanized scheduler reason, renamed knowledge sort copy, worker identity copy,
and the old detached GPU detail selector). The GPU detail selector was updated
and the focused GPU test is green. The other four assertions remain recorded
as full-suite work and are not represented as passing in this hotfix.

## 2026-07-26 single workstation Ollama consolidation

Decision:

- one loopback-only Windows Ollama daemon at `127.0.0.1:11434`;
- one active model store at `E:\AI\Models\Ollama\generation\models`;
- Hermes, portal embedding, and portal knowledge editing use that daemon;
- portal containers use only Docker Desktop's
  `http://host.docker.internal:11434` gateway;
- legacy Compose Ollama services remain stopped behind `legacy-ollama`; their
  files were not deleted and are rollback-only.

Implementation:

- added the bounded `ollama_host` workload type to the GPU scheduler;
- configured one `windows-ollama-generation` allowlist entry using the exact
  installed `ollama.exe`;
- added a read-only portal fallback plus a model-name-scoped graceful unload
  adapter so the UI remained truthful during a safe scheduler restart;
- allowed only the special Docker Desktop host gateway in the model-provider
  URL validator while continuing to reject remote hosts, HTTPS, paths, query
  strings, and fragments;
- changed default and operational Compose URLs for embedding and generation to
  the Windows daemon;
- moved all Docker Ollama services behind the disabled `legacy-ollama` profile;
- removed temporary Ollama startup from curation, embedding reindex, knowledge
  dedup, and semantic recovery GPUQ wrappers;
- retired and disabled `\LocalKnowledgePortal\ReapGpuEmbeddingBatch`;
- removed obsolete portal Ollama cleanup entries from GPUQ configuration while
  preserving unrelated entries;
- changed backup model metadata to use the configured pinned model and digest,
  so a backup neither depends on nor wakes a model daemon;
- added ADR 0017 and updated repository, runbook, filesystem, service-data, and
  global Codex workstation rules.

Model-storage evidence:

```text
Windows ollama list (OLLAMA_MODELS=E:\AI\Models\Ollama\generation\models)
  qwen3-embedding:0.6b  ac6da0dfba84  639 MB
  qwen3.5:9b-q4_K_M     6488c96fa5fa  6.6 GB
  gemma4:12b            4eb23ef187e2  7.6 GB
  qwen3:14b             bdbd181c33f2  9.3 GB
  gemma4:e4b            c6eb396dbd59  9.6 GB

fsutil hardlink list (qwen configuration and 639,150,592-byte model blobs)
  PASS: each blob has one path under the rollback store and one path under the
        active generation store, backed by the same physical file.
  PASS: no model download or byte copy was performed.
```

Runtime evidence:

```text
Get-NetTCPConnection -State Listen -LocalPort 11434
  PASS: 127.0.0.1:11434, owning process 27376.
Win32_Process inspection
  PASS: one Windows ollama.exe server; ollama app.exe is its desktop launcher,
        not a second serving daemon.
Hermes connection inspection
  PASS: hermes_cli.main PID 23676 had an established loopback connection to
        127.0.0.1:11434.
docker ps --filter name=ollama
  PASS: zero running Docker Ollama containers.
GET /api/v1/gpu-queue/status
  PASS: exactly one external workload, windows-ollama-generation.
GET /health/ready
  PASS: status=ready, database=true, ollama=true, generation model
        qwen3.5:9b-q4_K_M.
```

Deployment incident and recovery:

1. The first unified deployment restarted API, worker, watcher, and collector,
   but the existing Pydantic allowlist rejected `host.docker.internal`.
2. The first rebuild attempt used Windows Compose against a WSL build context,
   so Docker interpreted `/home/kutae/src/local-knowledge-portal` below
   `C:\Docker\local-knowledge-portal` and retained the old image.
3. The validator was restricted to the exact Docker Desktop gateway and the
   image was rebuilt from WSL. API returned healthy, then worker, watcher,
   collector, and web recovered.
4. GPUQ was gracefully restarted only after managed and queued work reached
   zero; the external workload count changed from three legacy rows to one
   Windows row.

Commands and results:

```text
uv run ruff check settings/main and focused tests
  PASS
uv run pytest -q tests/unit/test_generation.py tests/unit/test_gpu_queue_proxy.py
  PASS: 17 passed in 0.53s
gpu-workload-scheduler full pytest run before operational-script-only changes
  PASS: 13 passed in 0.74s
sh -n on four GPU wrappers and backup-wsl-docker.sh
  PASS
PowerShell AST parse of configure-gpu-queue-control.ps1
  PASS
docker compose ... config --quiet
  PASS
docker compose ... build api
  PASS
docker compose ... build web
  PASS: Next.js production build
pnpm --filter @lkp/web lint
  PASS: strict TypeScript no-emit
pnpm --filter @lkp/web exec playwright test --grep "GPU queue shows host capacity"
  PASS: 1 passed
```

An accidental full Playwright invocation ran seven tests instead of the
focused test: two passed and five failed on pre-existing stale UI/fixture
expectations (responsive service heading visibility, humanized scheduler
reason, knowledge sort copy, worker identity copy, and an always-loaded model
assumption). The focused GPU test was corrected to accept both idle and loaded
states while requiring exactly one runtime. The full suite is not reported as
passing.

The final GPUQ cleanup-configuration restart was deliberately refused because
`local-voice-agent-vllm-omni-tts-poc` started between the zero-work check and
restart attempt. That managed workload was not stopped or cancelled. The
single external Ollama configuration was already active; only removal of
obsolete in-memory cleanup commands awaits the next safe scheduler restart.

Current result for this slice: **VERIFIED** for single-daemon routing, model
storage reuse, portal health, GPU dashboard visibility, and legacy-container
shutdown. No backup or restore test was rerun for this routing-only change.

## 2026-07-26 — persistent health and allowlisted service management

Implemented:

- a permanent health summary in the left navigation and a dedicated,
  localized **레포·서비스 관리** route;
- grouped health for registered Docker projects, databases, ComfyUI,
  AI-Toolkit, Ollama, and the GPU scheduler;
- an allowlisted Windows loopback manager with bounded commands, server-side
  bearer authentication, explicit confirmation, origin validation, and no
  arbitrary command/path endpoint;
- per-user logon startup through
  `\Codex\Local Knowledge Service Manager`;
- protected read-only controls for the portal itself, Ollama, and GPU safety
  scheduler, plus a ComfyUI queue-empty safe-stop guard;
- removal of the retired periodic GPU embedding reaper from the live aggregate
  health count, while retaining its parser for an explicitly re-enabled guard;
- an operator-owned registry that is not automatically expanded during normal
  discovery.

Executed evidence:

```text
PowerShell AST parse scripts/install-service-manager.ps1
  PASS
uv run ruff check focused manager/API/test files
  PASS
uv run pytest -q focused manager/proxy/catalog/runtime tests
  PASS: 22 passed
corepack pnpm --filter @lkp/web lint
  PASS
corepack pnpm --filter @lkp/web build
  PASS: /services included in production routes
scripts/install-service-manager.ps1
  PASS: localhost manager healthy; scheduled task Running
GET http://127.0.0.1:8791/api/health
  PASS
GET http://127.0.0.1:8010/api/v1/service-manager
  PASS: 11 registered, 11 healthy, 0 attention, 8 controllable
GET http://127.0.0.1:8010/api/v1/system/services
  PASS: 24/24 active catalog components healthy after excluding the retired
        snapshot-only reaper check
POST /api/v1/service-manager/ai-toolkit/start with confirmation
  PASS: idempotent, service remained healthy
POST without confirmation
  PASS: rejected HTTP 422
POST from an untrusted Origin
  PASS: rejected HTTP 403
uv run pytest -q tests/unit
  PASS: 141 passed
scripts/run-isolated-integration-tests.sh
  PASS: 31 passed; disposable database removed by exit trap
docker compose build api web
  PASS
docker compose up -d --no-deps api web
  PASS after retrying a transient concurrent web-container rename conflict
GET /health/ready
  PASS: schema 0015_repository_analysis, database and Ollama ready
GET http://127.0.0.1:3010/services
  PASS: HTTP 200
Playwright focused service-management scenario
  PASS: persistent health summary, grouped services, protected Ollama,
        ComfyUI confirmation and cancel
Live in-app browser
  PASS: sidebar 24/24 healthy; 레포·서비스 관리 11 healthy, 0 attention
AI-Toolkit controlled stop/start after verifying no matching GPU job
  PASS: stop accepted in 20,461 ms, port 8675 offline observed, start accepted
        in 238 ms, port 8675 recovered, final manager state healthy
```

The first live AI-Toolkit stop exposed a timeout-boundary defect: systemd
completed its bounded stop after about 20 seconds while the API proxy timed out
earlier. The UI service was immediately restarted and verified healthy. Status
and action timeouts were then split; action proxying now allows 150 seconds.
The same real stop/offline/start/online sequence passed through the portal API.
No active training reservation matched AI-Toolkit before either test. No
database, GPU generation job, or other project was stopped. No migration,
backup, or restore was needed for this control-plane-only change. No secret or
command registry is exposed in the API response.

Final result for this slice: **VERIFIED** for implementation, startup,
read-only status, authenticated proxying, safe idempotent start, production
build, isolated integration, focused browser E2E, and deployment. One
Playwright attempt started during the web-container replacement and its
fallback `pnpm` command was unavailable; after the deployed route returned
HTTP 200, the same scenario passed without source changes.

## 2026-07-26 — safe confirmation, ComfyUI control, and navigation hotfix

Observed failure:

- the host-manager log records `POST /api/services/ai-toolkit/stop` at
  `2026-07-26T05:13:53.866120+00:00` (14:13:53 KST), matching the reported
  service stop before the operator intentionally pressed the final action;
- the dialog's destructive action previously had initial focus and the API
  accepted a UI-only boolean confirmation;
- ComfyUI's HTTP endpoint responded while GPUQ contained no matching job, so
  the old handler displayed a healthy managed service but had no job ID to
  cancel.

Implemented:

- server-issued 90-second one-time confirmation tokens bound to service and
  action, consumed before forwarding, bounded to 128 pending tokens, with
  invalid and replayed tokens rejected;
- a cancel-first confirmation dialog: opening the dialog prepares a challenge,
  **취소** receives initial focus, and only an explicit **실행** sends control;
- explicit ComfyUI `unmanaged` detection plus an allowlisted safe-stop helper
  that checks its queue, exact port, command line, and process owner;
- grouped primary navigation with **지식**, **활동**, and **운영** second-level
  menus while retaining **현황** as the single top-level dashboard;
- a visible, independently scrolling sidebar health summary and a default-open
  right health/context panel on the overview at desktop widths.

Operational verification:

```text
AI-Toolkit recovery
  PASS: start accepted after the unintended stop; final state healthy.
ComfyUI unmanaged stop
  PASS: no matching GPUQ job and an empty Comfy queue were verified.
  PASS: registered safe stop returned exit code 0 and loopback port 8188 closed.
  PASS: final state offline, can_start=true, can_stop=false.
Portal readiness
  PASS: ready; database=true; Ollama=true.
Service summary
  PASS: 10 healthy, 1 attention. The one attention item is the intentionally
        stopped ComfyUI service, not a failed health probe.
```

Commands and results:

```text
uv run ruff check focused API/manager/test files
  PASS
uv run pytest -q tests/unit
  PASS: 145 passed
sh scripts/run-isolated-integration-tests.sh
  PASS: 31 passed; disposable database dropped by exit trap
corepack pnpm --filter @lkp/web lint
  PASS: strict TypeScript no-emit
corepack pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build
docker compose ... build web
  PASS
docker compose ... up -d --no-deps web
  PASS
Playwright focused service-confirmation scenario
  PASS: 1 passed; first click issued one challenge and zero control requests,
        cancel retained zero control requests, explicit 실행 issued exactly one.
Playwright full portal suite
  PASS: 10 passed in 13.5 seconds after updating the prior flat-navigation
        selectors to expand the new second-level groups.
Invalid confirmation token through the live API
  PASS: HTTP 409
Live in-app browser
  PASS: 취소 was the active control; dismissing the dialog left AI-Toolkit
        healthy. The overview showed the right health panel and 23/24 summary,
        and 운영 expanded to four second-level items.
```

No migration, database write, backup, or restore was required for this
control-plane and presentation-only hotfix. ComfyUI was intentionally left
stopped because that was the requested operator action. Final result for this
hotfix: **VERIFIED**.

The first full Playwright rerun reached its three-minute command limit because
six old scenarios still waited for now-hidden flat navigation buttons. That
was a test-contract defect introduced by the intentional grouped-navigation
change, not an application hang. The scenarios now expand their owning group
before selecting an item; the complete ten-scenario rerun passed.

## 2026-07-26 — all-project canonical article scheduling

Observed failure:

- the database contained 16 project keys, but only
  `local-knowledge-portal` had a canonical `project_article` revision;
- the hourly GPU curation path had historically run knowledge-candidate
  curation without consistently running project-article curation;
- the prior per-run slice was alphabetical, so a repository beyond the run
  limit could be starved indefinitely;
- projects without a canonical revision fell back to legacy extracted journal
  sections, which made unfinished content look like the requested current
  article.

Implemented:

- one shared due-state planner is now used by the API, UI status summary, and
  article worker for every discovered project key;
- missing, interrupted, failed, editor-revision-changed, and source-changed
  projects are ordered by state and oldest comparison time instead of by
  repository name;
- each attempt persists `processing`, `last_compared_at`, and an isolated
  `error` state before moving to the next project, preventing both silent
  failure and alphabetical starvation;
- the hourly scheduled task runs candidate curation and project-article
  curation in the same concrete GPUQ workload, but does not reserve the GPU
  when both due counts are zero;
- the UI exposes the real state (`최신`, `갱신 대기`, `편집 대기`,
  `재시도 대기`) for every project. A project without a revision shows an
  explicit editing panel and no longer presents stitched legacy extracts as a
  finished article;
- evidence-digest cache files remain resumable on the E: application-data
  tier, so a bounded or interrupted pass does not repeat completed model work;
- the same planner automatically includes newly indexed project keys, and the
  oldest-attempt ordering rotates fairly when future project count exceeds the
  configured per-run limit.

Commands and results:

```text
uv run ruff check focused project-article/API/test files
  PASS
uv run pytest -q tests/unit
  PASS: 150 passed
sh scripts/run-isolated-integration-tests.sh
  PASS: 31 passed; disposable database dropped by exit trap
corepack pnpm --filter @lkp/web lint
  PASS: strict TypeScript no-emit
corepack pnpm --filter @lkp/web build
  PASS: Next.js production build
docker compose ... build api web
  PASS
docker compose ... up -d --no-deps api web
  PASS
Playwright focused repository-analysis scenario
  PASS: 1 passed
Playwright full portal suite
  PASS: 10 passed in 14.2 seconds
GET /health/ready
  PASS: database and Ollama ready; schema 0017_repository_embeddings
Live in-app browser
  PASS: 16 project selectors were visible. local-knowledge-portal was marked
        갱신 대기 and the other 15 projects were marked 편집 대기.
  PASS: selecting local-voice-agent showed the explicit canonical-article
        editing panel rather than legacy stitched content.
```

Operational state at verification time:

```text
project articles: projects=16, current=0, due=16, missing=15,
                  failed=0, processing=0, per_run_limit=20
GPUQ job: 0a8abba5-43db-4c05-a24f-8115e039f3fc
workload: local-knowledge-portal-curate-cases-and-project-articles
state: queued
requested VRAM: 8192 MiB
estimated duration: 1800 seconds
```

The initial all-project backfill is **not** reported as complete. It is queued
behind an active user GPU workload and an earlier queued knowledge-dedup job;
neither was reordered or cancelled. Implementation, deployment, automatic
future-project discovery, fair scheduling, failure persistence, and the honest
pending UI are **VERIFIED**. Canonical article generation for the 15 missing
projects remains operationally pending until the GPUQ job actually completes.

## 2026-07-26 — ComfyUI start no longer blocks repository GPU work

Observed failure:

- pressing **기동** submitted a `comfyui-server` job requesting 26,000 MiB,
  priority 70, an eight-hour estimate, and a 24-hour maximum runtime;
- the host manager returned HTTP 200 for queue admission even though port 8188
  was still offline;
- GPUQ reported `head-blocks-backfill:26000>7195`, so the long-lived server
  reservation moved ahead of the 8,192 MiB repository curation job and would
  continue occupying capacity after startup;
- the first replacement start reached ComfyUI readiness, but the child process
  inherited the manager's captured output pipe and prevented the control
  request from returning.

Implemented:

- ComfyUI is now an allowlisted lightweight `http_process`; the UI server does
  not create a GPUQ reservation;
- the existing bridge remains fail-closed in `prompt_reservation` mode, so
  each actual image-generation prompt still enters GPUQ with the configured
  26,000 MiB estimate;
- startup uses an exact hidden task-owned process, canonical E: application
  and data paths, dedicated runtime logs, a bounded readiness probe, and
  duplicate-port protection;
- the service manager discards inherited output only for the registered
  detached HTTP-process start. ComfyUI output remains in its dedicated log
  files, so the API returns after readiness instead of waiting on the server
  lifetime;
- safe stop still checks the ComfyUI queue and validates the exact loopback
  listener command and owner;
- the UI description now states that only image-generation requests use the
  GPU queue.

Operational correction and verification:

```text
Canceled obsolete queued job
  PASS: 61fc8198-2e1d-40c2-898a-22decc1ba944 changed queued -> canceled.
  No running GPU job or unrelated reservation was stopped.
Service manager deployment
  PASS: loopback manager healthy after exact manager-process restart.
Safe stop/start through the portal API
  PASS: stop accepted in 4,864 ms; final state offline.
  PASS: start accepted in 15,438 ms; final state healthy.
ComfyUI health
  PASS: /system_stats HTTP 200.
GPU bridge
  PASS: ready=true, reservation_mode=prompt_reservation,
        requested_vram_mb=26000.
GPU scheduler
  PASS: active/queued comfyui-server jobs=0.
  PASS: while local-voice-agent was active, the repository job remained
        honestly queued with head-blocks-backfill:8192>6641.
  PASS: after that workload released capacity, repository job
        4522ef02-3295-41d2-a53f-549060dba6ee changed to running at
        2026-07-26T08:23:18.498462+00:00 with no queued job remaining.
  PASS: final GPU state was 10,238 MiB used, 21,950 MiB free, 82% utilization.
Portal continuity
  PASS: web 3010 HTTP 200; API 8010 HTTP 200; service summary 24/24 healthy.
```

Commands and test results:

```text
PowerShell AST parse
  PASS: start-comfyui.ps1 and install-service-manager.ps1
uv run ruff check focused manager/test files
  PASS
uv run pytest -q tests/unit/test_host_service_manager.py
  PASS: 9 passed
uv run pytest -q tests/unit
  PASS: 152 passed
corepack pnpm --filter @lkp/web lint
  PASS: strict TypeScript no-emit
corepack pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build
docker compose ... build web
  PASS
docker compose ... up -d --no-deps web
  PASS
Playwright focused service-management scenario
  PASS: 1 passed
Live in-app browser
  PASS: ComfyUI card showed 정상 and the prompt-level GPU queue description;
        browser console errors=0.
```

No database migration or source-data change was required. Final result for
this incident: **VERIFIED**.

## 2026-07-26 — ComfyUI prompt admission bypass

Observed failure:

- ComfyUI bridge health reported `ready=true` and
  `reservation_mode=prompt_reservation`;
- a real prompt nevertheless ran for 50.09 seconds without creating a
  `comfyui-prompt` GPUQ job;
- the bridge matched only `POST /prompt`, while current ComfyUI also exposes
  the duplicated `POST /api/prompt` submission route;
- the prior verification checked health and absence of the obsolete server
  reservation, but did not submit through the second route. That was a false
  positive for per-prompt admission.

Implemented:

- both `/prompt` and `/api/prompt` are now fail-closed protected paths;
- the front-end priority adapter recognizes both aliases;
- the obsolete server-managed flag now rejects prompts with HTTP 503 instead
  of silently bypassing per-prompt admission;
- the canonical manual launcher now starts the lightweight server directly,
  clears the obsolete flag, and the former server-wide GPUQ launcher exits
  with an actionable retirement error;
- bridge health exposes protected paths, intercepted/submitted/rejected
  counters, last external path, and the last GPUQ job ID.

Verification:

```text
ComfyUI bridge unit tests
  PASS: 12 tests.
Python compile
  PASS: __init__.py, bridge_config.py, worker.py.
PowerShell AST parse
  PASS: run-comfyui.ps1 and retired run-comfyui-gpuq.ps1.
Safe service restart
  PASS: queue running=0 and pending=0 before stop.
  PASS: stop exit_code=0; start exit_code=0; final HTTP state healthy.
Isolated CPU-only /api/prompt E2E
  PASS: prompt 218379ce-e780-4a24-b360-416685f8eb3d.
  PASS: GPUQ job e25436b3-66cc-4366-8cdc-a744e898b211 succeeded,
        workload=comfyui-prompt, requested_vram_mb=64, priority=0,
        exit_code=0.
Live middleware route probe
  PASS: malformed /api/prompt was intercepted and rejected with HTTP 400
        before queue submission.
Live 26,000 MiB admission proof
  PASS: prompt cae4e497-94e2-4636-8514-b13bafdae511 created queued GPUQ job
        34e10a98-9ce4-4199-8fe3-1d06fa3d2931.
  PASS: workload=comfyui-prompt, requested_vram_mb=26000, priority=0.
  PASS: the task-owned validation reservation was cancelled before activation;
        the request returned the expected HTTP 503 cancellation result.
Final safety state
  PASS: ComfyUI running=0, pending=0.
  PASS: GPUQ ComfyUI active=0, queued=0.
  PASS: live bridge ready=true, submitted_prompt_count=1,
        last_external_request_path=/api/prompt,
        last_gpuq_job_id=34e10a98-9ce4-4199-8fe3-1d06fa3d2931.
```

No model workflow was executed during the live admission proof, no unrelated
GPUQ reservation was stopped, and no database or source document changed.
Final result for this incident: **VERIFIED**.

## 2026-07-27 — Missing Codex project knowledge registration

Reported thread:
`019f93a0-305f-7fe2-bb1a-045e952ff6e9` (`wedding_picture`).

Observed:

- the hook collector had ingested 321 activities from the thread;
- 238 successful `apply_patch` events were attributed to `wedding_picture`,
  but the project had zero journal entries and no canonical project article;
- the journal gate incorrectly required a successful test/build event, the
  same standard used for reusable knowledge publication;
- fallback attribution used the last current-directory component, splitting
  one session into `wedding_picture`, `compyui_learning`, and `ai-toolkit`;
- the latest completed turn was 2026-07-27T01:30:58Z. Eight later file-change
  events through 2026-07-27T02:12:35Z remain intentionally in-progress until
  that turn emits `Stop`.

Implemented:

- project history now accepts meaningful successful file mutations as
  `OBSERVED_CHANGE` without weakening the reusable-case evidence gate;
- a test/build success in the same completed turn remains required for journal
  status `VERIFIED`;
- project attribution prefers changed-file repositories, then the session's
  first canonical repository;
- entries without canonical repository scope are retained as
  `OUT_OF_PROJECT_SCOPE` and excluded from project lists and article editing;
- a reversible one-time reassessment reopens previously discarded journal
  changes and corrects legacy folder-name attribution.

Verification:

```text
Ruff
  PASS: focused activity, API, article, and integration files.
Unit tests
  PASS: 184.
Isolated PostgreSQL integration tests
  PASS: 33; disposable database removed by the exit trap.
Docker build
  PASS: local-knowledge-portal-app:wsl.
Deployment
  PASS: API and hook-collector recreated; worker, watcher, DB, and existing GPU
        work were not restarted.
API readiness
  PASS: ready.
Historical journal reassessment v2
  PASS: reopened=154, skipped=122, activities_deleted=false,
        knowledge_cases_created=false.
Project scope reassessment v3
  PASS: reassigned=2, excluded=15, unchanged=223,
        journals_deleted=false.
Reported thread
  PASS: wedding_picture registered with 45 OBSERVED_CHANGE entries.
  PASS: legacy split keys no longer remain on that session.
Canonical article
  PENDING: missing/pending_editor, GPUQ job
           e56be616-ec4e-4f02-beb9-ef9ef72d6c67 queued at 8,192 MiB,
           priority 40. Existing higher-priority GPU work was not interrupted.
```

The registration and attribution repair is **VERIFIED**. The model-edited
canonical article is not reported as complete until the queued GPUQ job
succeeds; the eight changes in the still-open turn will trigger a later
source-change refresh after `Stop`.
