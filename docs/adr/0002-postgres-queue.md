# ADR 0002: PostgreSQL durable queue

Status: accepted

Jobs are leased with `FOR UPDATE SKIP LOCKED`, have bounded exponential retry, and return to the queue after lease expiry. Idempotency keys and per-document advisory locking prevent duplicate effects. Heartbeats make worker liveness observable.

RabbitMQ and Kafka are deliberately excluded until measured lock contention, multi-host fan-out, or independent event retention justifies them.
