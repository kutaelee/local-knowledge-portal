"""Canonical project articles with append-only revisions.

Revision ID: 0013_project_canonical_articles
Revises: 0012_block_semantic_dedup
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_project_canonical_articles"
down_revision = "0012_block_semantic_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 is a legacy migration that calls current Base.metadata.create_all().
    # A clean database can therefore already contain newly-added model tables
    # before Alembic reaches this revision. Existing databases do not. Guard
    # each additive table so both upgrade paths converge on the same schema.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "project_article" not in existing:
        op.create_table(
            "project_article",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_key", sa.String(length=200), nullable=False),
            sa.Column(
                "current_revision_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column(
                "source_hash", sa.String(length=64), nullable=False, server_default=""
            ),
            sa.Column(
                "status", sa.String(length=32), nullable=False, server_default="pending"
            ),
            sa.Column("last_compared_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("project_key", name="uq_project_article_project"),
        )
        op.create_index(
            "ix_project_article_updated",
            "project_article",
            [sa.text("updated_at DESC"), "project_key"],
        )
    if "project_article_revision" not in existing:
        op.create_table(
            "project_article_revision",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "article_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("project_article.id"),
                nullable=False,
            ),
            sa.Column(
                "previous_revision_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("project_article_revision.id"),
                nullable=True,
            ),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("standfirst", postgresql.JSONB(), nullable=False),
            sa.Column("sections", postgresql.JSONB(), nullable=False),
            sa.Column("sources", postgresql.JSONB(), nullable=False),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("model", sa.String(length=200), nullable=False),
            sa.Column("model_digest", sa.String(length=200), nullable=False),
            sa.Column("prompt_version", sa.String(length=200), nullable=False),
            sa.Column(
                "change_summary",
                postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "article_id",
                "revision_number",
                name="uq_project_article_revision_number",
            ),
        )
        op.create_index(
            "ix_project_article_revision_article_created",
            "project_article_revision",
            ["article_id", sa.text("created_at DESC")],
        )


def downgrade() -> None:
    # Revisions are derived, but downgrades must still be explicit. No automatic
    # destructive downgrade is provided for a workstation production database.
    pass
