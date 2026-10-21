from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from internal_assistant.models import Conversation, Message


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(
        self,
        conversation_id: int | None,
        user_id: str,
        channel_id: str,
        teams_conversation_id: str | None = None,
    ) -> Conversation:
        if conversation_id:
            conversation = self.session.get(Conversation, conversation_id)
            # Un conversation_id ajeno no da acceso al historial ni a la memoria de otro usuario.
            if conversation and conversation.user_id == user_id:
                return conversation

        conversation = Conversation(
            user_id=user_id,
            channel_id=channel_id,
            teams_conversation_id=teams_conversation_id,
            state={"clarification_attempts": 0},
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get_for_user(self, conversation_id: int, user_id: str) -> Conversation | None:
        conversation = self.session.get(Conversation, conversation_id)
        return conversation if conversation and conversation.user_id == user_id else None

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[tuple[Conversation, int]]:
        """Conversaciones del usuario con al menos un mensaje, la mas reciente primero."""
        counts = (
            select(Message.conversation_id, func.count(Message.id).label("message_count"))
            .group_by(Message.conversation_id)
            .subquery()
        )
        stmt = (
            select(Conversation, counts.c.message_count)
            .join(counts, counts.c.conversation_id == Conversation.id)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
        )
        return [(row[0], int(row[1])) for row in self.session.execute(stmt).all()]

    def rename(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def delete(self, conversation: Conversation) -> None:
        # Mensajes, memoria y feedback se borran en cascada; los retrieval_logs quedan anonimos (SET NULL).
        self.session.delete(conversation)
        self.session.flush()

    def save_state(self, conversation: Conversation, state: dict) -> Conversation:
        conversation.state = state
        self.session.add(conversation)
        self.session.flush()
        return conversation


class MessageRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        conversation_id: int,
        role: str,
        content: str,
        intent: str | None = None,
        created_ticket_id: int | None = None,
        meta: dict | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            intent=intent,
            created_ticket_id=created_ticket_id,
            meta=meta,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_all(self, conversation_id: int, *, limit: int = 200) -> list[Message]:
        stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def list_by_conversation(self, conversation_id: int, limit: int = 20) -> list[Message]:
        stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.desc()).limit(limit)
        return list(reversed(self.session.execute(stmt).scalars().all()))
