"""Store the complete source manifest separately from displayed citations.

Revision ID: 0014_article_source_manifest
Revises: 0013_project_canonical_articles
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_article_source_manifest"
down_revision = "0013_project_canonical_articles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        item["name"]
        for item in inspector.get_columns("project_article_revision")
    }
    if "source_manifest" not in columns:
        op.add_column(
            "project_article_revision",
            sa.Column(
                "source_manifest",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )


def downgrade() -> None:
    # Project article revisions are append-only derived records. Avoid an
    # automatic destructive downgrade on the workstation production database.
    pass
