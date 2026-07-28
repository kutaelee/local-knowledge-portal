"""Enforce locale-specific developer feed post limits.

Revision ID: 0020_developer_feed_limits
Revises: 0019_developer_feed
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_developer_feed_limits"
down_revision = "0019_developer_feed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_check_constraints(
            "developer_feed_post"
        )
    }
    if "ck_developer_feed_content_ko_140" not in existing:
        op.create_check_constraint(
            "ck_developer_feed_content_ko_140",
            "developer_feed_post",
            "char_length(content_ko) <= 140",
        )
    if "ck_developer_feed_content_en_280" not in existing:
        op.create_check_constraint(
            "ck_developer_feed_content_en_280",
            "developer_feed_post",
            "char_length(content_en) <= 280",
        )


def downgrade() -> None:
    existing = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_check_constraints(
            "developer_feed_post"
        )
    }
    if "ck_developer_feed_content_en_280" in existing:
        op.drop_constraint(
            "ck_developer_feed_content_en_280",
            "developer_feed_post",
            type_="check",
        )
    if "ck_developer_feed_content_ko_140" in existing:
        op.drop_constraint(
            "ck_developer_feed_content_ko_140",
            "developer_feed_post",
            type_="check",
        )
