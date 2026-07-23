# Worker recovery runbook

If a worker exits while processing, its lease expires. Another worker can
claim the same job, increment the attempt count, and continue at-least-once
processing. The handler must remain idempotent.

Error signature:

`worker lease expired before completion`

After PostgreSQL restarts, collectors keep raw events in the E drive spool and
retry database ingestion.
