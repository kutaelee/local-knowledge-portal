"""add project development journal and ordered-list indexes

Revision ID: 0007_project_journal_pagination
Revises: 0006_chunk_content_trigram
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_project_journal_pagination"
down_revision = "0006_chunk_content_trigram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("project_journal_entry"):
        op.create_table(
            "project_journal_entry",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_stop_activity_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("project_key", sa.String(length=200), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("intent", sa.Text(), nullable=False),
            sa.Column("change_summary", sa.Text(), nullable=False),
            sa.Column(
                "failures",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column("resolution", sa.Text(), nullable=False),
            sa.Column(
                "verification",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "changed_files",
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::text[]"),
            ),
            sa.Column(
                "knowledge_references",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "significance_reasons",
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::text[]"),
            ),
            sa.Column(
                "verification_status",
                sa.String(length=32),
                nullable=False,
                server_default="UNVERIFIED",
            ),
            sa.Column(
                "metadata",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
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
            sa.ForeignKeyConstraint(
                ["source_stop_activity_id"],
                ["activity_event.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_stop_activity_id",
                name="uq_project_journal_source_stop",
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_journal_project_occurred "
        "ON project_journal_entry (project_key, occurred_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_journal_occurred "
        "ON project_journal_entry (occurred_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_activity_occurred_id "
        "ON activity_event (occurred_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_candidate_status_created_id "
        "ON knowledge_candidate (status, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_case_status_last_seen_id "
        "ON knowledge_case (status, last_seen_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_state_modified_id "
        "ON document (state, modified_at_fs DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_project_path_id "
        "ON document (project_key, project_relative_path, id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_version_document_detected_id "
        "ON document_version (document_id, detected_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_backup_created_id "
        "ON backup_run (created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_job_created_id "
        "ON ingest_job (created_at DESC, id DESC)"
    )


def downgrade() -> None:
    raise RuntimeError("destructive downgrade is intentionally unsupported")
