"""add indexed lexical candidate paths

Revision ID: 0005_search_candidate_indexes
Revises: 0004_project_semantic_scope
Create Date: 2026-07-24
"""

from alembic import op

revision = "0005_search_candidate_indexes"
down_revision = "0004_project_semantic_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunk_symbol_lower
        ON document_chunk (lower(symbol_name))
        WHERE symbol_name IS NOT NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
