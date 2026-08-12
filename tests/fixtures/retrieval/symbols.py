def lease_job_with_skip_locked(worker_id: str) -> str:
    """Lease one durable PostgreSQL queue row for a worker."""
    return f"lease:{worker_id}:FOR UPDATE SKIP LOCKED"
