"""index current document relations

Revision ID: 0011_document_link_indexes
Revises: 0010_knowledge_dedup_vectors
"""

from alembic import op

revision = "0011_document_link_indexes"
down_revision = "0010_knowledge_dedup_vectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_document_link_source",
        "document_link",
        ["source_document_id", "source_line"],
    )
    op.create_index(
        "ix_document_link_target",
        "document_link",
        ["target_document_id"],
        postgresql_where="target_document_id IS NOT NULL",
    )
    op.create_index(
        "ix_document_link_unresolved_target",
        "document_link",
        ["raw_target"],
        postgresql_where="target_document_id IS NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_document_link_unresolved_target", table_name="document_link")
    op.drop_index("ix_document_link_target", table_name="document_link")
    op.drop_index("ix_document_link_source", table_name="document_link")
