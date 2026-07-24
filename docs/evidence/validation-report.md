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
