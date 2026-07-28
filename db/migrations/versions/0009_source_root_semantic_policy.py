"""add source-root semantic-only exclusions

Revision ID: 0009_source_root_semantic_policy
Revises: 0008_knowledge_navigation
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_source_root_semantic_policy"
down_revision = "0008_knowledge_navigation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("source_root")}
    if "semantic_exclude_patterns" in columns:
        return
    op.add_column(
        "source_root",
        sa.Column(
            "semantic_exclude_patterns",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
