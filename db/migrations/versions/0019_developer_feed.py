"""Add the evidence-bound bilingual developer feed.

Revision ID: 0019_developer_feed
Revises: 0018_repository_checkpoints
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_developer_feed"
down_revision = "0018_repository_checkpoints"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "developer_feed_post" in tables:
        return
    op.create_table(
        "developer_feed_post",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("publication_key", sa.String(300), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("post_type", sa.String(32), nullable=False),
        sa.Column(
            "thread_root_id",
            UUID,
            sa.ForeignKey("developer_feed_post.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "reply_to_id",
            UUID,
            sa.ForeignKey("developer_feed_post.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content_ko", sa.String(280), nullable=False),
        sa.Column("content_en", sa.String(280), nullable=False),
        sa.Column("source_manifest", JSONB, nullable=False),
        sa.Column("source_embedding_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_embedding_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persona_version", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "publication_key", name="uq_developer_feed_publication_key"
        ),
    )
    op.create_index(
        "ix_developer_feed_created",
        "developer_feed_post",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_developer_feed_project_created",
        "developer_feed_post",
        ["project_key", "created_at"],
    )
    op.create_index(
        "ix_developer_feed_thread",
        "developer_feed_post",
        ["thread_root_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_developer_feed_thread", table_name="developer_feed_post")
    op.drop_index(
        "ix_developer_feed_project_created", table_name="developer_feed_post"
    )
    op.drop_index("ix_developer_feed_created", table_name="developer_feed_post")
    op.drop_table("developer_feed_post")
