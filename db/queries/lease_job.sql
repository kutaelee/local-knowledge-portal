WITH candidate AS (
  SELECT id
  FROM ingest_job
  WHERE (
    (status IN ('pending', 'failed') AND available_at <= now())
    OR (status IN ('leased', 'processing') AND lease_expires_at < now())
  )
  AND attempt_count < max_attempts
  ORDER BY priority, available_at, created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE ingest_job AS job
SET status = 'leased',
    leased_by = :worker_id,
    lease_expires_at = now() + make_interval(secs => :lease_seconds),
    attempt_count = attempt_count + 1,
    updated_at = now()
FROM candidate
WHERE job.id = candidate.id
RETURNING job.*;
