"""add rebuildable repository knowledge retrieval embeddings

Revision ID: 0017_repository_embeddings
Revises: 0016_repository_design
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0017_repository_embeddings"
down_revision = "0016_repository_design"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "repository_knowledge_embedding" in tables:
        return
    op.create_table(
        "repository_knowledge_embedding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repository_knowledge_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding_text_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_revision", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("model_digest", sa.String(length=200), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "knowledge_item_id",
            "embedding_revision",
            name="uq_repository_knowledge_embedding_item_revision",
        ),
    )
    op.create_index(
        "ix_repository_knowledge_embedding_revision",
        "repository_knowledge_embedding",
        ["embedding_revision"],
    )
    op.execute(
        "CREATE INDEX ix_repository_knowledge_embedding_hnsw "
        "ON repository_knowledge_embedding USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
