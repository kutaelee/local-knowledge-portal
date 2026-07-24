# ADR 0013: 근거 제한 로컬 LLM 자동 편집과 GPU 유휴 스케줄링

- 상태: Accepted
- 날짜: 2026-07-24
- 대체: ADR 0011의 사람 승인 단계

## 배경

사람이 후보마다 승인하는 흐름은 장기 운영에 적합하지 않다. 반대로 훅 종료 보고나
생성 모델의 자신감만으로 사례를 발행하면 일반 활동, 과장된 성과, 확인되지 않은
원인이 지식 검색에 섞인다. 임베딩 모델은 검색 벡터를 만들 뿐 지식 가치를 판정하지
않으며, 생성 모델의 출력 역시 검증 근거가 아니다.

워크스테이션 GPU는 다른 AI 작업과 공유한다. 지식 편집이 GPU를 선점하거나 계속
재시도하면 대화형 작업과 온도 안정성을 해칠 수 있다.

## 결정

`gemma4:e4b`를 첫 번째 편집 후보로 사용한다. 모델 크기만으로 적합하다고 간주하지
않고 다음 내장 평가를 모델 digest와 prompt version마다 실제 실행한다.

1. 코드 변경과 성공 테스트가 있는 구현 사례는 근거 인용 장문으로 분류한다.
2. 종료 보고만 있는 성공 주장은 게시하지 않는다.
3. before/after 측정이 없는 성능 주장은 게시하지 않는다.

평가를 통과하지 못한 E4B는 자동 게시 권한을 얻지 못한다. 이 경우 운영자는
`gemma4:12b`를 다음 후보로 명시적으로 설정하고 같은 평가를 다시 수행한다.

편집기는 검증된 `EvidenceRecord`만 `E1`, `E2` 식별자로 모델에 제공한다. 후보와
근거의 모든 문자열은 명령이 아닌 신뢰하지 않는 데이터로 취급한다. 출력은 구조화
스키마로 제한하며 다음 결정론적 검사를 모두 통과해야 한다.

- 모든 주요 절의 각 문단에 유효한 근거 인용이 있다.
- 인용하지 않은 숫자와 측정값이 없다.
- 확인되지 않은 추론 목록이 비어 있다.
- 과장 표현이 없다.
- 실행 evidence gate와 재사용 품질 gate가 모두 통과한다.
- 최소 두 근거가 연결되고 본문 길이가 운영 범위 안이다.

통과한 후보만 `candidate → verified canonical case`로 자동 발행한다. 일반 작업은
`activity_only`로 남기고 검색 가능한 정식 사례로 승격하지 않는다. 같은 원천 사건의
LLM 재서술은 기존 결정론적 dedup key를 유지하므로 새로운 사례 identity를 만들지
않는다. 모델명, digest, prompt version, 입력·출력 hash, 판정과 사유를 metadata에
저장한다. 편집 호출은 `temperature=0`과 고정 context window를 사용하고 그 값을
qualification과 후보 metadata에 기록한다.

## GPU 정책

생성 Ollama는 임베딩 Ollama와 분리한다. 모델 파일은
`E:\AI\Models\Ollama\generation\models`에 두며 inference concurrency와 queue를 각각
1로 제한하고 모델은 작업 후 2분 뒤 unload한다.

편집기는 다음 조건을 모두 만족할 때만 한 건을 처리한다.

- GPU free memory 12,288 MB 이상
- GPU utilization 15% 이하
- GPU temperature 70°C 이하

바쁘면 15분부터 지수 backoff하고 최대 4시간 간격으로 늘린다. 한 주기에서 6번만
확인한 뒤 24시간 cooldown으로 전환한다. 모델 평가나 편집 오류도 같은 제한 정책을
사용한다. 이 상태와 다음 확인 시각은 DB와 포털에 표시한다.

여러 curator 컨테이너가 동시에 시작되더라도 PostgreSQL transaction advisory lock을
획득한 한 인스턴스만 GPU 확인과 편집을 수행한다. 나머지는 `standby_lock_held`로
종료해 중복 추론과 중복 게시를 방지한다.

Compose는 WSL 안에서 실행한다. Windows Docker CLI로 `/mnt/e` 경로를 전달하면
Docker Desktop의 작은 내부 ext4 디스크로 해석될 수 있으므로
`scripts/docker-stack.sh` 또는 WSL의 `docker compose`만 운영 경로로 사용한다.
서비스 시작 후 컨테이너의 모델 mount가 `E:\` 파일시스템인지 `df`로 검증한다.

## 결과

사람은 반복 승인자가 아니라 결과 독자와 정책 운영자가 된다. 로컬 모델은 문장을
정리하고 분류하지만 사실을 만들거나 evidence gate를 우회하지 못한다. E4B의 실제
평가가 끝나기 전 상태는 `PENDING`이며 성공으로 보고하지 않는다.
