from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from internal_assistant.config import get_settings
from internal_assistant.db.base import Base


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_chunks_source_chunk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(get_settings().embedding_dimensions))
    full_text_tsvector: Mapped[str | None] = mapped_column(TSVECTOR)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(255), index=True)
    affected_system: Mapped[str | None] = mapped_column(String(64), index=True)
    department: Mapped[str | None] = mapped_column(String(64), index=True)
    document_type: Mapped[str | None] = mapped_column(String(64), index=True)
    incident_status: Mapped[str | None] = mapped_column(String(32), index=True)
    is_resolved: Mapped[bool | None] = mapped_column(Boolean, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String()), default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
