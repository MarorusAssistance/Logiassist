from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from internal_assistant.models import ConversationMemory
from internal_assistant.observability.tracing import set_span_attributes, start_span


@dataclass(slots=True)
class RetrievedMemory:
    memory_id: int
    conversation_id: int
    message_id: int
    role: str
    memory_text: str
    summary: str
    metadata: dict
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "role": self.role,
            "memory_text": self.memory_text,
            "summary": self.summary,
            "metadata": self.metadata,
            "score": self.score,
        }


class ConversationMemoryRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_for_message(
        self,
        *,
        conversation_id: int,
        message_id: int,
        role: str,
        memory_text: str,
        summary: str,
        metadata: dict,
        embedding: list[float],
    ) -> ConversationMemory:
        existing = self.session.execute(
            select(ConversationMemory).where(ConversationMemory.message_id == message_id)
        ).scalar_one_or_none()
        if existing:
            existing.role = role
            existing.memory_text = memory_text
            existing.summary = summary
            existing.metadata_ = metadata
            existing.embedding = embedding
            self.session.add(existing)
            self.session.flush()
            return existing

        item = ConversationMemory(
            conversation_id=conversation_id,
            message_id=message_id,
            role=role,
            memory_text=memory_text,
            summary=summary,
            metadata_=metadata,
            embedding=embedding,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def search(
        self,
        *,
        conversation_id: int,
        query_embedding: list[float],
        limit: int = 5,
        exclude_message_id: int | None = None,
    ) -> list[RetrievedMemory]:
        with start_span(
            "memory.vector_search",
            {
                "conversation_id": conversation_id,
                "memory.limit": limit,
                "memory.embedding_dimensions": len(query_embedding),
                "memory.exclude_message_id": exclude_message_id or 0,
            },
        ) as span:
            stmt = text(
                """
                SELECT id, conversation_id, message_id, role, memory_text, summary, metadata,
                       1 - (embedding <=> CAST(:embedding AS vector)) AS score
                FROM conversation_memories
                WHERE conversation_id = :conversation_id
                  AND embedding IS NOT NULL
                  AND (:exclude_message_id IS NULL OR message_id <> :exclude_message_id)
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            )
            rows = self.session.execute(
                stmt,
                {
                    "conversation_id": conversation_id,
                    "embedding": query_embedding,
                    "limit": limit,
                    "exclude_message_id": exclude_message_id,
                },
            ).mappings().all()
            memories = [
                RetrievedMemory(
                    memory_id=row["id"],
                    conversation_id=row["conversation_id"],
                    message_id=row["message_id"],
                    role=row["role"],
                    memory_text=row["memory_text"],
                    summary=row["summary"],
                    metadata=row["metadata"] or {},
                    score=float(row["score"] or 0.0),
                )
                for row in rows
            ]
            scores = [memory.score for memory in memories]
            set_span_attributes(
                span,
                {
                    "memory.result_count": len(memories),
                    "memory.ids": [memory.memory_id for memory in memories],
                    "memory.message_ids": [memory.message_id for memory in memories],
                    "memory.score_min": min(scores) if scores else 0.0,
                    "memory.score_max": max(scores) if scores else 0.0,
                },
            )
            return memories

    def list_recent(self, conversation_id: int, limit: int = 6) -> list[ConversationMemory]:
        stmt = (
            select(ConversationMemory)
            .where(ConversationMemory.conversation_id == conversation_id)
            .order_by(ConversationMemory.created_at.desc())
            .limit(limit)
        )
        return list(reversed(self.session.execute(stmt).scalars().all()))
