"""Store repository components, lifecycle, and dependency usages.

Revision ID: 0016_repository_design
Revises: 0015_repository_analysis
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_repository_design"
down_revision = "0015_repository_analysis"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "repository_component" in inspector.get_table_names():
        return

    op.create_table(
        "repository_component",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("component_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("component_type", sa.String(80), nullable=False),
        sa.Column("responsibility", sa.Text(), nullable=False),
        sa.Column("relative_paths", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("entry_points", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "component_key",
            name="uq_repository_component_key",
        ),
    )
    op.create_index(
        "ix_repository_component_snapshot",
        "repository_component",
        ["snapshot_id", "component_type"],
    )

    op.create_table(
        "repository_lifecycle_node",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("component_key", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "node_key",
            name="uq_repository_lifecycle_node_key",
        ),
    )
    op.create_index(
        "ix_repository_lifecycle_node_order",
        "repository_lifecycle_node",
        ["snapshot_id", "sequence"],
    )

    op.create_table(
        "repository_lifecycle_edge",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(40), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
    )
    op.create_index(
        "ix_repository_lifecycle_edge_snapshot",
        "repository_lifecycle_edge",
        ["snapshot_id", "source_key", "target_key"],
    )

    op.create_table(
        "repository_dependency_usage",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dependency_name", sa.Text(), nullable=False),
        sa.Column("component_key", sa.Text(), nullable=False),
        sa.Column("usage_type", sa.String(80), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("provenance", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_repository_dependency_usage_lookup",
        "repository_dependency_usage",
        ["snapshot_id", "dependency_name", "component_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repository_dependency_usage_lookup",
        table_name="repository_dependency_usage",
    )
    op.drop_table("repository_dependency_usage")
    op.drop_index(
        "ix_repository_lifecycle_edge_snapshot",
        table_name="repository_lifecycle_edge",
    )
    op.drop_table("repository_lifecycle_edge")
    op.drop_index(
        "ix_repository_lifecycle_node_order",
        table_name="repository_lifecycle_node",
    )
    op.drop_table("repository_lifecycle_node")
    op.drop_index(
        "ix_repository_component_snapshot",
        table_name="repository_component",
    )
    op.drop_table("repository_component")
