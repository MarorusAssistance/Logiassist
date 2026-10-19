from __future__ import annotations

from sqlalchemy import select
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

    def save_state(self, conversation: Conversation, state: dict) -> Conversation:
        conversation.state = state
        self.session.add(conversation)
        self.session.flush()
        return conversation


class MessageRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, conversation_id: int, role: str, content: str, intent: str | None = None, created_ticket_id: int | None = None) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            intent=intent,
            created_ticket_id=created_ticket_id,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_by_conversation(self, conversation_id: int, limit: int = 20) -> list[Message]:
        stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.desc()).limit(limit)
        return list(reversed(self.session.execute(stmt).scalars().all()))
