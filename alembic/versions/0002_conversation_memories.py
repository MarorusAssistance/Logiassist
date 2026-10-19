"""conversation memories

Revision ID: 0002_conversation_memories
Revises: 0001_initial_schema
Create Date: 2026-05-08 14:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from internal_assistant.config import get_settings


revision = "0002_conversation_memories"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _embedding_dimensions() -> int:
    return get_settings().embedding_dimensions


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("memory_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding", Vector(_embedding_dimensions()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", name="uq_conversation_memories_message_id"),
    )
    op.create_index("ix_conversation_memories_conversation_id", "conversation_memories", ["conversation_id"])
    op.create_index("ix_conversation_memories_message_id", "conversation_memories", ["message_id"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_memories_embedding "
        "ON conversation_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversation_memories_embedding")
    op.drop_index("ix_conversation_memories_message_id", table_name="conversation_memories")
    op.drop_index("ix_conversation_memories_conversation_id", table_name="conversation_memories")
    op.drop_table("conversation_memories")
