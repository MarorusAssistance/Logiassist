"""usage events for rate and budget limits

Revision ID: 0004_usage_events
Revises: 0003_chunk_metadata_filters
Create Date: 2026-10-21 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_usage_events"
down_revision = "0003_chunk_metadata_filters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_key", sa.String(length=64), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_usage_events_client_key", "usage_events", ["client_key"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_client_key", table_name="usage_events")
    op.drop_table("usage_events")
