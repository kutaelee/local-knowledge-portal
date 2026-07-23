"""initial integrated schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-23
"""
from alembic import op

from lkp.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    Base.metadata.create_all(bind=op.get_bind())
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_lexical ON document_chunk "
        "USING gin (lexical_search_vector)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_embedding_hnsw ON chunk_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lkp_chunk_tsvector_update() RETURNS trigger AS $$
        BEGIN
          NEW.lexical_search_vector :=
            to_tsvector('simple', coalesce(NEW.heading_path, '') || ' ' ||
                                  coalesce(NEW.symbol_name, '') || ' ' ||
                                  coalesce(NEW.content, ''));
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunk_tsvector_update
        BEFORE INSERT OR UPDATE OF content, heading_path, symbol_name
        ON document_chunk FOR EACH ROW EXECUTE FUNCTION lkp_chunk_tsvector_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chunk_tsvector_update ON document_chunk")
    op.execute("DROP FUNCTION IF EXISTS lkp_chunk_tsvector_update")
    Base.metadata.drop_all(bind=op.get_bind())
