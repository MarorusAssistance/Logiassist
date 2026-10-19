from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from internal_assistant.llm.streaming import ChatStreamEvent
from internal_assistant.schemas.chat import AssistantDecision, ChatPlan


class LLMProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        raise NotImplementedError

    def stream_chat_response(
        self,
        *,
        question: str,
        context_chunks: list[dict],
        conversation_state: dict,
    ) -> Iterator[ChatStreamEvent]:
        decision = self.generate_chat_response(
            question=question,
            context_chunks=context_chunks,
            conversation_state=conversation_state,
        )
        if decision.answer:
            yield ChatStreamEvent(kind="token", text=decision.answer)
        yield ChatStreamEvent(kind="final", decision=decision)

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        return ChatPlan.fallback(message)
