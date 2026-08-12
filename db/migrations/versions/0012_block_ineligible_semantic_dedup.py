"""Block semantic dedup work that cannot pass the evidence gate.

Revision ID: 0012_block_semantic_dedup
Revises: 0011_document_link_indexes
"""

from alembic import op

revision = "0012_block_semantic_dedup"
down_revision = "0011_document_link_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE knowledge_candidate
        SET metadata = jsonb_set(
            metadata,
            '{semantic_dedup,state}',
            '"blocked_by_evidence_gate"'::jsonb,
            true
        )
        WHERE evidence_gate_status <> 'VERIFIED'
          AND metadata #>> '{semantic_dedup,state}' = 'pending_gpu_vector_check'
        """
    )


def downgrade() -> None:
    # The normalized value is derived queue state, not source evidence. Restoring
    # it to pending on downgrade would requeue ineligible GPU work.
    pass
