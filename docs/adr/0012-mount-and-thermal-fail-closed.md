# ADR 0012: WSL bind mount와 임베딩 부하를 fail closed로 운영

- 상태: Accepted
- 날짜: 2026-07-24
- 대체: ADR 0008의 Ollama 1 CPU 기본값

## 배경

운영 Compose를 WSL repository가 아닌 다른 현재 디렉터리에서 호출했을 때 Docker
Desktop 컨테이너에 `/data`와 `/home/kutae/src`라는 빈 디렉터리가 연결됐다. API
readiness는 DB와 Ollama만 확인해 정상으로 보고했고, worker는 실제 변경 파일을 읽지
못한 job을 종료했다. 원본 E: 파일과 WSL repository에는 손상이 없었다.

한국어 canonical page 3건을 다시 색인할 때 Ollama는 1 CPU quota의 98–104%를
지속적으로 사용했다. 이전에 사용자가 92°C를 관측했으며 서비스는 신뢰할 수 있는
온도 센서를 읽을 수 없으므로 1 CPU를 안전 기본값으로 계속 간주할 수 없다.

## 결정

- Compose는 WSL에서 repository로 `cd`한 뒤 실행한다.
- `E:\Data\LocalKnowledgePortal\.lkp-runtime-root`와 repository의
  `pyproject.toml`을 각 app 컨테이너에 file sentinel로 mount한다.
- API readiness와 worker/watcher/collector 시작은 두 sentinel이 실제 파일인지
  검사한다. 빈 bind directory면 정상으로 기동하지 않는다.
- bootstrap은 runtime sentinel을 기존 파일을 덮어쓰지 않고 최초 한 번 생성한다.
- Ollama 기본 quota를 `0.5 CPU`로 낮춘다. worker와 API는 1 CPU, watcher,
  collector, web은 0.5 CPU를 유지한다.
- model concurrency 1, embedding batch 1, pause file, bounded queue/cooldown 정책은
  유지한다.
- quota 증가는 실제 온도 센서, 대표 부하, 30분 thermal soak, rollback 증거가
  있을 때만 검토한다.

## 검증

- 잘못 연결된 컨테이너에서 `/data`와 `/home/kutae/src`가 비어 있음을 관측했다.
- 같은 source를 WSL repository cwd에서 재생성한 뒤 vault 파일 11개, source
  repository 2개, Codex session/spool directory를 확인했다.
- live API와 worker에서 두 sentinel 검사를 통과했다.
- 0.5 CPU에서 실제 embedding retry 동안 Ollama 사용률은 48.02%, 49.15%,
  49.44%였고 68초 안에 성공했다. 완료 직후 0%로 복귀했다.
- 현재 파일 SHA-256과 DB document hash가 일치하고 canonical page 3건의 current
  chunk/vector가 각각 `10/10`, `10/10`, `10/10`임을 확인했다.

## 결과

DB만 살아 있고 source/data mount가 비어 있는 거짓 readiness가 차단된다. 온도를 직접
관측하지 못하는 환경에서는 throughput보다 보수적인 전력 상한을 우선한다.
