"""align conversation memory embeddings with EMBEDDING_DIMENSIONS and use HNSW

Si la base se creo con otro modelo de embeddings, la columna se quedaba con la dimension vieja
y cada turno fallaba al indexarse en memoria (se registraba como warning y la memoria quedaba
vacia). Los embeddings de memoria se pueden regenerar, asi que si la dimension no cuadra se
vacian y se cambia el tipo. Ademas se cambia ivfflat por HNSW: ivfflat construido sobre una
tabla vacia o pequena devuelve resultados incompletos.

Revision ID: 0006_memory_embedding_dims
Revises: 0005_conversation_titles
Create Date: 2026-10-21 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from internal_assistant.config import get_settings


revision = "0006_memory_embedding_dims"
down_revision = "0005_conversation_titles"
branch_labels = None
depends_on = None


def _current_dimensions() -> int | None:
    type_name = op.get_bind().execute(
        sa.text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'conversation_memories'::regclass AND attname = 'embedding'"
        )
    ).scalar()
    if not type_name or "(" not in type_name:
        return None
    return int(type_name.split("(", 1)[1].rstrip(")"))


def upgrade() -> None:
    dimensions = get_settings().embedding_dimensions
    op.execute("DROP INDEX IF EXISTS ix_conversation_memories_embedding")
    if _current_dimensions() != dimensions:
        op.execute("UPDATE conversation_memories SET embedding = NULL")
        op.execute(f"ALTER TABLE conversation_memories ALTER COLUMN embedding TYPE vector({dimensions})")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_memories_embedding "
        "ON conversation_memories USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversation_memories_embedding")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_memories_embedding "
        "ON conversation_memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
