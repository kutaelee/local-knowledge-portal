---
tags: [architecture, queue, postgres]
---

# ADR: PostgreSQL durable queue

We chose `FOR UPDATE SKIP LOCKED` so multiple workers can lease jobs without
RabbitMQ or Kafka. PostgreSQL remains the durable queue until measured lock
contention or multi-host fan-out requires another broker.

The reason for this decision is operational simplicity: metadata, job history,
leases, and search state share one transactional system.
