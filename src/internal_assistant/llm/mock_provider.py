from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256

from internal_assistant.config import get_settings
from internal_assistant.llm.base import LLMProvider
from internal_assistant.llm.streaming import ChatStreamEvent
from internal_assistant.rag.filters import RetrievalFilters
from internal_assistant.schemas.chat import AssistantDecision, ChatPlan


class MockLLMProvider(LLMProvider):
    provider_name = "mock"

    def __init__(
        self,
        clarification_phrase: str = "Necesito un poco mas de detalle para responder con seguridad.",
        *,
        embedding_dimensions: int = 512,
    ) -> None:
        self.settings = get_settings()
        self.clarification_phrase = clarification_phrase
        self.embedding_dimensions = embedding_dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            base = list(digest)
            vector = [float(base[idx % len(base)]) / 255.0 for idx in range(self.embedding_dimensions)]
            vectors.append(vector)
        return vectors

    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        conversation_memory = conversation_state.get("_conversation_memory") or []
        if conversation_memory and not context_chunks:
            top_memories = conversation_memory[:2]
            answer = "Basandome en la memoria conversacional:\n" + "\n".join(
                f"- {memory.get('memory_text', '')[:220]}" for memory in top_memories
            )
            return AssistantDecision(
                answer=answer,
                needs_clarification=False,
                clarification_question=None,
                should_offer_incident=False,
                used_chunk_ids=[],
            )

        if not context_chunks:
            return AssistantDecision(
                answer=self.clarification_phrase,
                needs_clarification=True,
                clarification_question="Puedes indicar el sistema o el proceso exacto?",
                should_offer_incident=False,
                used_chunk_ids=[],
            )

        top_chunks = context_chunks[:2]
        answer = "Segun la evidencia recuperada:\n" + "\n".join(
            f"- {chunk['content'][:220]}" for chunk in top_chunks
        )
        return AssistantDecision(
            answer=answer,
            needs_clarification=False,
            clarification_question=None,
            should_offer_incident=False,
            used_chunk_ids=[chunk["chunk_id"] for chunk in top_chunks],
        )

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
        words = decision.answer.split(" ")
        for index, word in enumerate(words):
            suffix = " " if index < len(words) - 1 else ""
            yield ChatStreamEvent(kind="token", text=f"{word}{suffix}")
        yield ChatStreamEvent(kind="final", decision=decision)

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        normalized = message.strip().lower()
        recent_text = " ".join(str(item.get("content", "")) for item in recent_messages).lower()
        follow_up = any(
            phrase in normalized
            for phrase in (
                "esa respuesta",
                "no me vale",
                "explicamelo mejor",
                "explícamelo mejor",
                "teniendo en cuenta",
                "lo anterior",
                "como te dije",
            )
        )
        recall_only = any(
            phrase in normalized
            for phrase in (
                "que te dije",
                "qué te dije",
                "que dije antes",
                "resumeme lo anterior",
                "resúmeme lo anterior",
            )
        )
        if recall_only:
            return ChatPlan(
                intent="question_answering",
                needs_conversation_memory=True,
                needs_knowledge_index=False,
                can_answer_from_conversation_only=True,
                should_ask_clarification_first=False,
                conversation_memory_query=message,
                knowledge_index_query="",
                user_context_summary="El usuario pide recuperar informacion de la conversacion.",
                expected_source_preference=[],
                mentioned_systems=[],
                retrieval_filters=RetrievalFilters(),
                filter_reason="",
                reason="mock_recall_only",
            )
        if follow_up:
            query = message
            if "onboarding" in recent_text or "operaciones" in recent_text:
                query = "procedimiento de onboarding e iniciacion para nuevo empleado del equipo de operaciones"
            return ChatPlan(
                intent="question_answering",
                needs_conversation_memory=True,
                needs_knowledge_index=True,
                can_answer_from_conversation_only=False,
                should_ask_clarification_first=False,
                conversation_memory_query=query,
                knowledge_index_query=query,
                user_context_summary="El usuario reformula una respuesta anterior.",
                expected_source_preference=["document"],
                mentioned_systems=[],
                retrieval_filters=RetrievalFilters(source_types=["document"], departments=["Onboarding", "Operaciones"]),
                filter_reason="follow-up sobre onboarding/operaciones",
                reason="mock_follow_up",
            )
        if any(phrase in normalized for phrase in ("como se resolvio", "cómo se resolvió", "que correccion", "qué corrección")):
            return ChatPlan(
                intent="question_answering",
                needs_conversation_memory=False,
                needs_knowledge_index=True,
                can_answer_from_conversation_only=False,
                should_ask_clarification_first=False,
                conversation_memory_query="",
                knowledge_index_query=message,
                user_context_summary="El usuario pregunta por una incidencia resuelta.",
                expected_source_preference=["incident"],
                mentioned_systems=[],
                retrieval_filters=RetrievalFilters(source_types=["incident"], incident_statuses=["resolved"], is_resolved=True),
                filter_reason="peticion de caso resuelto",
                reason="mock_resolved_case",
            )
        if any(phrase in normalized for phrase in ("pasos", "procedimiento", "como registro", "cómo registro", "como completar", "cómo completar")):
            return ChatPlan(
                intent="question_answering",
                needs_conversation_memory=False,
                needs_knowledge_index=True,
                can_answer_from_conversation_only=False,
                should_ask_clarification_first=False,
                conversation_memory_query="",
                knowledge_index_query=message,
                user_context_summary="El usuario pide procedimiento documentado.",
                expected_source_preference=["document"],
                mentioned_systems=[],
                retrieval_filters=RetrievalFilters(source_types=["document"], document_types=["procedimiento"]),
                filter_reason="peticion de procedimiento",
                reason="mock_procedure",
            )
        return ChatPlan.fallback(message)
