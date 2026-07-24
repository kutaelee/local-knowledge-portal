"""add indexed knowledge navigation

Revision ID: 0008_knowledge_navigation
Revises: 0007_project_journal_pagination
Create Date: 2026-07-24
"""

from alembic import op

revision = "0008_knowledge_navigation"
down_revision = "0007_project_journal_pagination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_project_category_latest "
        "ON knowledge_case ((metadata->>'project'), category, last_seen_at DESC, id DESC) "
        "WHERE status = 'verified'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_tags_gin "
        "ON knowledge_case USING gin ((metadata->'tags')) "
        "WHERE status = 'verified'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_source_catalog "
        "ON document (source_root_id, state, project_key, project_relative_path, id)"
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
