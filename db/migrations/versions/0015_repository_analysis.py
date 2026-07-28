"""Add provenance-preserving repository analysis domain.

Revision ID: 0015_repository_analysis
Revises: 0014_article_source_manifest
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_repository_analysis"
down_revision = "0014_article_source_manifest"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "repository_project" in inspector.get_table_names():
        return

    op.create_table(
        "repository_project",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("canonical_name", sa.String(200), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("local_source_reference", sa.Text(), nullable=False),
        sa.Column("repository_origin_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_repository_project_status",
        "repository_project",
        ["status", "updated_at"],
    )

    op.create_table(
        "repository_snapshot",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("repository_project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_name", sa.String(240), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("git_commit", sa.String(64), nullable=True),
        sa.Column("git_branch", sa.String(240), nullable=True),
        sa.Column("dirty_worktree", sa.Boolean(), nullable=False),
        sa.Column("analysis_base_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("build_systems", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_hash",
            name="uq_repository_snapshot_project_source",
        ),
    )
    op.create_index(
        "ix_repository_snapshot_latest",
        "repository_snapshot",
        ["project_id", "created_at"],
    )

    op.create_table(
        "repository_source_file",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("module", sa.String(240), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "relative_path",
            name="uq_repository_source_file_path",
        ),
    )
    op.create_index(
        "ix_repository_source_file_language",
        "repository_source_file",
        ["snapshot_id", "language"],
    )

    op.create_table(
        "repository_source_symbol",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("symbol_type", sa.String(80), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("signature_hash", sa.String(64), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "ix_repository_symbol_lookup",
        "repository_source_symbol",
        ["snapshot_id", "symbol_type"],
    )

    op.create_table(
        "repository_source_relation",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_symbol", sa.Text(), nullable=False),
        sa.Column("target_symbol", sa.Text(), nullable=False),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("provenance", sa.String(40), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_repository_relation_lookup",
        "repository_source_relation",
        ["snapshot_id", "relation_type"],
    )

    op.create_table(
        "repository_configuration_reference",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("config_key", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("declaration_line", sa.Integer(), nullable=True),
        sa.Column("referenced_by", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("has_default", sa.Boolean(), nullable=False),
        sa.Column("runtime_value_verified", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_repository_config_lookup",
        "repository_configuration_reference",
        ["snapshot_id"],
    )

    op.create_table(
        "repository_dependency_artifact",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("artifact_type", sa.String(60), nullable=False),
        sa.Column("classification", sa.String(60), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(80), nullable=True),
        sa.Column("analysis_status", sa.String(60), nullable=False),
    )

    op.create_table(
        "repository_analysis_job",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("repository_project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("correlation_id", UUID, nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("analysis_version", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("model_quantization", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "repository_analysis_task",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("repository_analysis_job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(80), nullable=False),
        sa.Column("seed_symbol", sa.Text(), nullable=True),
        sa.Column("scope", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "repository_analysis_claim",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("repository_analysis_job.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(80), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("related_configs", JSONB, nullable=False),
        sa.Column("assumptions", JSONB, nullable=False),
        sa.Column("unknowns", JSONB, nullable=False),
        sa.Column("counter_evidence", JSONB, nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("validation_errors", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_repository_claim_validation",
        "repository_analysis_claim",
        ["job_id", "validation_status"],
    )

    op.create_table(
        "repository_knowledge_item",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", UUID, sa.ForeignKey("document.id"), nullable=True),
        sa.Column("knowledge_type", sa.String(80), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("processing_steps", JSONB, nullable=False),
        sa.Column("components", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("configurations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("dependencies", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("source_references", JSONB, nullable=False),
        sa.Column("validation_status", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("unknowns", JSONB, nullable=False),
        sa.Column("analysis_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("searchable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_repository_knowledge_searchable",
        "repository_knowledge_item",
        ["snapshot_id", "searchable", "knowledge_type"],
    )

    op.create_table(
        "repository_evaluation_case",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id",
            UUID,
            sa.ForeignKey("repository_project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_type", sa.String(80), nullable=False),
        sa.Column("expected_evidence", JSONB, nullable=False),
        sa.Column("required_files", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("acceptable_answer", sa.Text(), nullable=False),
        sa.Column("forbidden_assertions", JSONB, nullable=False),
        sa.Column("grading_criteria", JSONB, nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("scenario_type", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "repository_evaluation_result",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "case_id",
            UUID,
            sa.ForeignKey("repository_evaluation_case.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            UUID,
            sa.ForeignKey("repository_snapshot.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("failure_category", sa.String(100), nullable=True),
        sa.Column("details", JSONB, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    # Repository analysis records are derived but may contain reviewed evaluation
    # evidence. Rollback disables the feature and keeps the additive tables.
    pass
