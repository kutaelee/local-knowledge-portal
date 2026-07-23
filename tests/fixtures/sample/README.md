---
tags: [knowledge-base, fixture]
owner: test
---

# Scanner fixture

이 문서는 로컬 지식베이스의 scanner, queue, worker, chunk, embedding, 검색 흐름을 검증한다.

## Decision

We chose PostgreSQL row locking because a separate broker is unnecessary for the initial local workload.

## Runbook

If a worker lease expires, another worker can claim the job using `FOR UPDATE SKIP LOCKED`.
