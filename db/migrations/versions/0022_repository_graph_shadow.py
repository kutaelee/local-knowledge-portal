"""Add fail-closed repository graph shadow tables.

Revision ID: 0022_repository_graph_shadow
Revises: 0021_repository_read_indexes
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_repository_graph_shadow"
down_revision = "0021_repository_read_indexes"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    op.create_table(
        "repository_graph_node",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("qualified_symbol", sa.Text(), nullable=False),
        sa.Column("symbol_leaf", sa.Text(), nullable=False),
        sa.Column("symbol_type", sa.String(80), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("node_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "node_fingerprint",
            name="uq_repository_graph_node_fingerprint",
        ),
        sa.CheckConstraint("start_line >= 1", name="ck_repository_graph_node_start"),
        sa.CheckConstraint(
            "end_line >= start_line",
            name="ck_repository_graph_node_end",
        ),
    )
    op.create_index(
        "ix_repository_graph_node_symbol",
        "repository_graph_node",
        ["snapshot_id", "qualified_symbol"],
    )
    op.create_index(
        "ix_repository_graph_node_leaf",
        "repository_graph_node",
        ["snapshot_id", "symbol_leaf"],
    )
    op.create_index(
        "ix_repository_graph_node_path",
        "repository_graph_node",
        ["snapshot_id", "relative_path"],
    )

    op.create_table(
        "repository_graph_edge",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_node_id",
            UUID,
            sa.ForeignKey("repository_graph_node.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_node_id",
            UUID,
            sa.ForeignKey("repository_graph_node.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("extractor", sa.String(100), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("resolution_status", sa.String(40), nullable=False),
        sa.Column("verification_status", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("navigation_only", sa.Boolean(), nullable=False),
        sa.Column("edge_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "edge_fingerprint",
            name="uq_repository_graph_edge_fingerprint",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_repository_graph_edge_confidence",
        ),
    )
    op.create_index(
        "ix_repository_graph_edge_outgoing",
        "repository_graph_edge",
        ["snapshot_id", "source_node_id", "relation_type", "navigation_only"],
    )
    op.create_index(
        "ix_repository_graph_edge_reverse",
        "repository_graph_edge",
        ["snapshot_id", "target_node_id", "relation_type", "navigation_only"],
    )
    op.create_index(
        "ix_repository_graph_edge_gate",
        "repository_graph_edge",
        [
            "snapshot_id",
            "resolution_status",
            "verification_status",
            "navigation_only",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_repository_graph_edge_gate", table_name="repository_graph_edge")
    op.drop_index("ix_repository_graph_edge_reverse", table_name="repository_graph_edge")
    op.drop_index("ix_repository_graph_edge_outgoing", table_name="repository_graph_edge")
    op.drop_table("repository_graph_edge")
    op.drop_index("ix_repository_graph_node_path", table_name="repository_graph_node")
    op.drop_index("ix_repository_graph_node_leaf", table_name="repository_graph_node")
    op.drop_index("ix_repository_graph_node_symbol", table_name="repository_graph_node")
    op.drop_table("repository_graph_node")
