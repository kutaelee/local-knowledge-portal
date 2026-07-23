"""activity, evidence, and canonical knowledge cases

Revision ID: 0002_activity_knowledge
Revises: 0001_initial
Create Date: 2026-07-23
"""

from alembic import op
from lkp.models import Base
from sqlalchemy import Column, String, inspect, text

revision = "0002_activity_knowledge"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("source_root")}
    if "data_scope" not in columns:
        op.add_column(
            "source_root",
            Column(
                "data_scope",
                String(length=32),
                nullable=False,
                server_default="production",
            ),
        )
    bind.execute(
        text(
            "UPDATE source_root SET data_scope = 'validation' "
            "WHERE source_type IN ('validation', 'test', 'fixture')"
        )
    )
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
