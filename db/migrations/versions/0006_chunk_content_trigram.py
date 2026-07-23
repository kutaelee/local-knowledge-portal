"""add trigram acceleration for bounded content fallback

Revision ID: 0006_chunk_content_trigram
Revises: 0005_search_candidate_indexes
"""

from alembic import op

revision = "0006_chunk_content_trigram"
down_revision = "0005_search_candidate_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunk_content_trgm
        ON document_chunk USING gin (content gin_trgm_ops)
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "destructive downgrade is unsupported; restore into a new database instead"
    )
