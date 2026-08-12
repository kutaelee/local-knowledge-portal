"""separate repository project paths from semantic scope

Revision ID: 0004_project_semantic_scope
Revises: 0003_queue_scale_indexes
Create Date: 2026-07-24
"""

from alembic import op

revision = "0004_project_semantic_scope"
down_revision = "0003_queue_scale_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document
        ADD COLUMN IF NOT EXISTS project_relative_path TEXT
        """
    )
    op.execute(
        """
        UPDATE document
        SET project_relative_path = relative_path
        WHERE project_relative_path IS NULL
        """
    )
    op.execute(
        """
        ALTER TABLE document
        ALTER COLUMN project_relative_path SET NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_project_path
        ON document (project_key, project_relative_path)
        """
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
