# Validation report

검증 시각: 2026-07-23 (Asia/Seoul)

저장소: `C:\Dev\Repos\local-knowledge-portal`

최종 판정: **VERIFIED**

장시간 watcher endurance, 장시간 부하 유지, 실제 Windows 장시간 절전/복귀만 제외했다.
Codex `/hooks` trust는 제품 보안 경계 때문에 **MANUAL_APPROVAL_REQUIRED**이며, 사용자가
허용한 유일한 수동 승인 항목이다. 애플리케이션은 WSL2가 아니라 Windows native
프로세스로 실행하며 PostgreSQL만 Docker Desktop의 Linux 컨테이너를 사용한다.

## 구현 결과

- PostgreSQL 18.4 + pgvector 0.8.2, schema `0002_activity_knowledge`.
- Windows source repository는 C:, 운영 데이터·Ollama model·raw spool·로그는 E:,
  immutable backup은 D:에 둔다.
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

- Ollama Windows stable: `0.32.1`.
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
uv run alembic upgrade head
  PASS: 0002_activity_knowledge
uv run ruff check services scripts tests
  PASS
uv run pytest tests/unit -q
  PASS: 19 passed
LKP_TEST_DATABASE_URL=<dedicated DB> uv run pytest tests/integration -q
  PASS: 6 passed, 1 dependency deprecation warning
pnpm --filter @lkp/web build
  PASS: Next.js 16.2.11 production build
docker compose --env-file .env -f infra/docker/compose.yaml --profile app build api
  PASS: manifest list sha256:5bf874c340d7bd776ba4be51996e480df7261264082c185129e9a54e801921ed
PLAYWRIGHT_BROWSERS_PATH=E:\Cache\ms-playwright pnpm --filter @lkp/web test
  PASS: 3 passed
uv run python tests/retrieval/evaluate.py
  PASS: keyword/semantic/hybrid baseline, no-answer, stale exclusion
scripts\backup.ps1
  PASS: D:\Backups\LocalKnowledgePortal\database\2026-07-23T193330
scripts\restore-test.ps1 -BackupDirectory <above>
  PASS: temporary DB lkp_restore_20260723193404
```

Playwright는 다음을 실제 확인했다.

1. Overview, Explorer, document version timeline, latest diff, provenance/citation.
2. Activity list/detail과 reported/verified 분리.
3. Knowledge Case list/detail, occurrences/revisions, Candidate evidence gate.
4. keyword/semantic/hybrid 결과.
5. failed/dead-letter job과 retry.
6. worker heartbeat와 backup 상태.

최종 backup manifest:

- dump size: 295,673,237 bytes.
- SHA-256:
  `77e36a88942f60b5e8611acc1a27aad1b5415a674f2406d1333302fa12bc18d8`.
- source configuration hash:
  `8322feb87b6917fb94aa3712ce04127c36714aadb13bf9ae5dbf71e932b4c5b8`.
- restore count: documents 7,861, chunks 69,140, vectors 49,081.
- schema `0002_activity_knowledge`, unvalidated foreign key 0, content query 1,649,
  canonical duplicate assertion 통과, activities 11 / unverified 6.
- 설정, managed vault, raw event, source/model/pipeline manifest도 같은 immutable timestamp
  경로에 저장했다. temp restore DB는 검증 후 제거했다.

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
