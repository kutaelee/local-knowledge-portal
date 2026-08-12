"""Add resumable, non-searchable repository analysis checkpoints.

Revision ID: 0018_repository_checkpoints
Revises: 0017_repository_embeddings
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_repository_checkpoints"
down_revision = "0017_repository_embeddings"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "repository_analysis_checkpoint" in tables:
        return

    op.create_table(
        "repository_analysis_checkpoint",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("snapshot_id", UUID, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("analysis_fingerprint", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("model_quantization", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("planned_task_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "snapshot_id",
            "analysis_fingerprint",
            name="uq_repository_checkpoint_snapshot_fingerprint",
        ),
    )
    op.create_index(
        "ix_repository_checkpoint_resume",
        "repository_analysis_checkpoint",
        ["snapshot_id", "analysis_fingerprint", "status"],
    )

    op.create_table(
        "repository_analysis_task_checkpoint",
        sa.Column(
            "checkpoint_id",
            UUID,
            sa.ForeignKey("repository_analysis_checkpoint.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("task_id", UUID, primary_key=True),
        sa.Column("analysis_unit", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("outcome", JSONB, nullable=False),
        sa.Column("claims", JSONB, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "checkpoint_id",
            "analysis_unit",
            name="uq_repository_task_checkpoint_unit",
        ),
    )
    op.create_index(
        "ix_repository_task_checkpoint_status",
        "repository_analysis_task_checkpoint",
        ["checkpoint_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_task_checkpoint_status",
        table_name="repository_analysis_task_checkpoint",
    )
    op.drop_table("repository_analysis_task_checkpoint")
    op.drop_index(
        "ix_repository_checkpoint_resume",
        table_name="repository_analysis_checkpoint",
    )
    op.drop_table("repository_analysis_checkpoint")
