# ADR 0015: 프로젝트 중심 위키, 다중 태그와 최신 상태 재생성

- 상태: Accepted
- 날짜: 2026-07-24
- 관련 ADR: 0006, 0009, 0013, 0014

## 배경

원본 저장소의 모든 파일을 지식 사례로 취급하면 코드 카탈로그와 재사용 가능한
노하우가 섞인다. 반대로 사례를 단일 목록으로만 게시하면 어떤 프로젝트의 현재
상태인지, 어떤 상황에서 쓸 수 있는지 찾기 어렵다. 지식 승격과 사람이 읽는 위키는
같은 검색 저장소를 사용하되 목적과 생명주기를 구분해야 한다.

다음 OSS의 공개 구조에서 재사용 가능한 패턴을 검토했다.

- [Pratiyush/llm-wiki](https://github.com/Pratiyush/llm-wiki): 저장소 단위 문서 생성과
  탐색 구조.
- [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki): 원본에서 생성 문서로 이어지는
  단계 분리.
- [atomicstrata/llm-wiki-compiler](https://github.com/atomicstrata/llm-wiki-compiler):
  목적·구조를 명시한 컴파일식 생성.
- [nvk/llm-wiki](https://github.com/nvk/llm-wiki): 작은 Markdown 단위와 사람이 읽는
  산출물.

해당 프로젝트의 데이터 모델이나 런타임을 가져오지 않고, 프로젝트 경계·재생성 가능한
출력·원본 추적·증분 갱신 패턴만 적용한다.

## 결정

### 서로 다른 두 층

1. `Document`와 `DocumentChunk`는 원본 저장소 탐색, lexical 검색과 provenance를
   제공하는 파일 카탈로그다. 코드 파일이 여기에 보이는 것은 정상이다.
2. `KnowledgeCase`는 검증된 문제·원인·조치·결과만 담는 재사용 지식이다. 일반 활동이나
   파일 존재 사실만으로 생성하지 않는다.

두 층은 UI 메뉴와 필터에서 분리하되, 사례 페이지도 provenance를 위해 같은 문서
인덱싱 파이프라인을 통과한다.

### 프로젝트와 태그

관리형 사례 frontmatter와 chunk metadata에 다음 분류를 기록한다.

- `project:<project-key>`
- `case:<category>`
- `situation:<category>`
- `lifecycle:verified`
- `knowledge-value:promote`

프로젝트·상황·생명주기 태그는 동시에 보존한다. 검색 API는 프로젝트와 여러 태그의
`all`/`any` 필터를 제공하고 결과에 사용된 태그와 provenance를 반환한다.

### 최신 프로젝트 문서

`_generated/Projects/<project>/overview.md`는 해당 프로젝트의 현재 canonical case만
참조해 결정론적으로 재생성한다. 파일은 원자적으로 교체하고 내용 hash가 같으면
재작성하지 않는다. 사례가 승격·병합·revision될 때 overview도 갱신하며, 사람이 작성한
Vault 파일은 건드리지 않는다.

동일 내용 hash의 문서를 다시 처리할 때 새 `document_version`이나 vector를 만들지
않지만, frontmatter의 프로젝트와 태그는 반드시 다시 동기화한다. 이 규칙은 분류
하니스가 바뀐 뒤 파생 metadata만 교정할 수 있게 한다.

### 모델과 코드의 책임

모델은 이미 검증된 evidence를 사람이 읽기 좋은 한국어 문맥으로 편집할 뿐이다.
후보 상태, 게시 자격, dedup/occurrence, revision, 태그, 파일 경로, DB 갱신과 overview
재생성은 코드가 소유한다. 모델이 제안한 분류나 `publish` 판단은 상태 전이 권한이
없다.

## 결과

파일 탐색은 저장소의 실제 구조를 유지하면서, 지식 사례는 프로젝트·상황·태그로
좁혀서 찾을 수 있다. 프로젝트 overview는 최신 verified revision만 반영하고 언제든
DB와 원본 evidence로 재생성할 수 있다. 분류 변경은 append-only 사례 revision과
idempotent metadata resync로 적용하며 기존 문서 버전이나 원본 파일을 삭제하지 않는다.
