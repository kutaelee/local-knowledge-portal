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
  PASS: 18 passed, 1 Starlette dependency deprecation warning

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
