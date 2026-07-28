"""Enforce locale-specific developer feed post limits.

Revision ID: 0020_developer_feed_limits
Revises: 0019_developer_feed
"""

from alembic import op

revision = "0020_developer_feed_limits"
down_revision = "0019_developer_feed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_developer_feed_content_ko_140",
        "developer_feed_post",
        "char_length(content_ko) <= 140",
    )
    op.create_check_constraint(
        "ck_developer_feed_content_en_280",
        "developer_feed_post",
        "char_length(content_en) <= 280",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_developer_feed_content_en_280",
        "developer_feed_post",
        type_="check",
    )
    op.drop_constraint(
        "ck_developer_feed_content_ko_140",
        "developer_feed_post",
        type_="check",
    )
