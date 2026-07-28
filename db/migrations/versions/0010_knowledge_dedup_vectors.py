"""add rebuildable knowledge candidate/case similarity vectors

Revision ID: 0010_knowledge_dedup_vectors
Revises: 0009_source_root_semantic_policy
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0010_knowledge_dedup_vectors"
down_revision = "0009_source_root_semantic_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "knowledge_similarity_embedding" in tables:
        return
    op.create_table(
        "knowledge_similarity_embedding",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "key_terms",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "record_type",
            "record_id",
            "embedding_revision",
            name="uq_knowledge_similarity_embedding_record_revision",
        ),
    )
    op.create_index(
        "ix_knowledge_similarity_embedding_lookup",
        "knowledge_similarity_embedding",
        ["record_type", "embedding_revision"],
    )
    op.create_index(
        "ix_knowledge_similarity_embedding_terms",
        "knowledge_similarity_embedding",
        ["key_terms"],
        postgresql_using="gin",
    )
    op.execute(
        "CREATE INDEX ix_knowledge_similarity_embedding_hnsw "
        "ON knowledge_similarity_embedding USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
