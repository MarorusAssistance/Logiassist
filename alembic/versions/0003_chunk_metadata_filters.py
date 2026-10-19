"""chunk metadata filters

Revision ID: 0003_chunk_metadata_filters
Revises: 0002_conversation_memories
Create Date: 2026-05-17 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_chunk_metadata_filters"
down_revision = "0002_conversation_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("source_title", sa.String(length=255), nullable=True))
    op.add_column("chunks", sa.Column("affected_system", sa.String(length=64), nullable=True))
    op.add_column("chunks", sa.Column("department", sa.String(length=64), nullable=True))
    op.add_column("chunks", sa.Column("document_type", sa.String(length=64), nullable=True))
    op.add_column("chunks", sa.Column("incident_status", sa.String(length=32), nullable=True))
    op.add_column("chunks", sa.Column("is_resolved", sa.Boolean(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
    )
    op.create_index("ix_chunks_source_title", "chunks", ["source_title"])
    op.create_index("ix_chunks_affected_system", "chunks", ["affected_system"])
    op.create_index("ix_chunks_department", "chunks", ["department"])
    op.create_index("ix_chunks_document_type", "chunks", ["document_type"])
    op.create_index("ix_chunks_incident_status", "chunks", ["incident_status"])
    op.create_index("ix_chunks_is_resolved", "chunks", ["is_resolved"])
    op.create_index("ix_chunks_tags", "chunks", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_chunks_tags", table_name="chunks")
    op.drop_index("ix_chunks_is_resolved", table_name="chunks")
    op.drop_index("ix_chunks_incident_status", table_name="chunks")
    op.drop_index("ix_chunks_document_type", table_name="chunks")
    op.drop_index("ix_chunks_department", table_name="chunks")
    op.drop_index("ix_chunks_affected_system", table_name="chunks")
    op.drop_index("ix_chunks_source_title", table_name="chunks")
    op.drop_column("chunks", "tags")
    op.drop_column("chunks", "is_resolved")
    op.drop_column("chunks", "incident_status")
    op.drop_column("chunks", "document_type")
    op.drop_column("chunks", "department")
    op.drop_column("chunks", "affected_system")
    op.drop_column("chunks", "source_title")
