"""conversation titles and assistant message metadata

Revision ID: 0005_conversation_titles
Revises: 0004_usage_events
Create Date: 2026-10-21 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_conversation_titles"
down_revision = "0004_usage_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("title", sa.String(length=120), nullable=True))
    op.add_column("messages", sa.Column("meta", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "meta")
    op.drop_column("conversations", "title")
