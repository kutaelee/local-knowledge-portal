"""add non-destructive queue and history indexes

Revision ID: 0003_queue_scale_indexes
Revises: 0002_activity_knowledge
Create Date: 2026-07-23
"""

from alembic import op

revision = "0003_queue_scale_indexes"
down_revision = "0002_activity_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingest_job_claim_ready
        ON ingest_job (priority, available_at, created_at)
        WHERE status IN ('pending', 'failed')
          AND attempt_count < max_attempts
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingest_job_claim_expired
        ON ingest_job (lease_expires_at)
        WHERE status IN ('leased', 'processing')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingest_job_status_created
        ON ingest_job (status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingest_event_created
        ON ingest_event (created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_ingest_event_type_created
        ON ingest_event (event_type, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_hook_spool_status_created
        ON hook_spool_event (status, created_at DESC)
        """
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
