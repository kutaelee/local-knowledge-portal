"""Add repository analysis read-path indexes.

Revision ID: 0021_repository_read_indexes
Revises: 0020_developer_feed_limits
"""

from alembic import op

revision = "0021_repository_read_indexes"
down_revision = "0020_developer_feed_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_repository_symbol_name_lookup",
        "repository_source_symbol",
        ["snapshot_id", "symbol"],
    )
    op.create_index(
        "ix_repository_analysis_job_provenance",
        "repository_analysis_job",
        [
            "snapshot_id",
            "analysis_version",
            "model",
            "model_quantization",
            "prompt_version",
            "status",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_analysis_job_provenance",
        table_name="repository_analysis_job",
    )
    op.drop_index(
        "ix_repository_symbol_name_lookup",
        table_name="repository_source_symbol",
    )
