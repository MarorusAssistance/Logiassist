from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from internal_assistant.db.base import Base


class UsageEvent(Base):
    """Una peticion de chat ya atendida: base de los limites de uso por cliente y globales."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
