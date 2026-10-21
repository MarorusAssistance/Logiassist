from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from internal_assistant.rag.filters import RetrievalFilters, normalize_retrieval_filters


class SourceSnippet(BaseModel):
    source_type: str
    source_id: int
    title: str
    source_url: str | None = None
    excerpt: str
    chunk_id: int
    # True si el modelo cito algun chunk de esta fuente (used_chunk_ids); False si solo se consulto.
    used: bool = False


class AssistantDecision(BaseModel):
    answer: str
    needs_clarification: bool = False
    clarification_question: str | None = None
    should_offer_incident: bool = False
    used_chunk_ids: list[int] = Field(default_factory=list)

    @field_validator("used_chunk_ids", mode="before")
    @classmethod
    def normalize_used_chunk_ids(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        normalized = []
        for item in value:
            if isinstance(item, int):
                normalized.append(item)
                continue
            match = re.search(r"\d+", str(item))
            if match:
                normalized.append(int(match.group(0)))
                continue
            normalized.append(item)
        return normalized


class ChatPlan(BaseModel):
    intent: str = "question_answering"
    # La pregunta no tiene relacion con la operacion de la empresa: se rechaza sin buscar.
    out_of_scope: bool = False
    needs_conversation_memory: bool = False
    needs_knowledge_index: bool = True
    can_answer_from_conversation_only: bool = False
    should_ask_clarification_first: bool = False
    conversation_memory_query: str = ""
    knowledge_index_query: str = ""
    user_context_summary: str = ""
    expected_source_preference: list[str] = Field(default_factory=list)
    mentioned_systems: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    retrieval_filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    filter_reason: str = ""
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def recover_misplaced_filters(cls, data):
        if not isinstance(data, dict):
            return data
        expected = data.get("expected_source_preference")
        if isinstance(expected, dict):
            data = dict(data)
            if not data.get("retrieval_filters"):
                data["retrieval_filters"] = expected
            data["expected_source_preference"] = expected.get("source_types") or []
        return data

    @field_validator(
        "conversation_memory_query",
        "knowledge_index_query",
        "user_context_summary",
        "filter_reason",
        "reason",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value):
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return value

    @field_validator("expected_source_preference", "mentioned_systems", "search_keywords", mode="before")
    @classmethod
    def normalize_string_lists(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, dict):
            source_types = value.get("source_types")
            if isinstance(source_types, list):
                return [str(item).strip() for item in source_types if str(item).strip()]
            return []
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    source_types = item.get("source_types")
                    if isinstance(source_types, list):
                        normalized.extend(str(source_type).strip() for source_type in source_types if str(source_type).strip())
                    continue
                item_text = str(item).strip()
                if item_text:
                    normalized.append(item_text)
            return normalized
        return value

    @field_validator("retrieval_filters", mode="before")
    @classmethod
    def normalize_filters(cls, value):
        return normalize_retrieval_filters(value)

    @classmethod
    def fallback(cls, message: str) -> "ChatPlan":
        return cls(
            intent="question_answering",
            needs_conversation_memory=False,
            needs_knowledge_index=True,
            can_answer_from_conversation_only=False,
            should_ask_clarification_first=False,
            conversation_memory_query="",
            knowledge_index_query=message,
            user_context_summary="",
            expected_source_preference=[],
            mentioned_systems=[],
            retrieval_filters=RetrievalFilters(),
            filter_reason="",
            reason="fallback_safe_knowledge_search",
        )

    @model_validator(mode="after")
    def normalize_plan(self) -> "ChatPlan":
        if self.can_answer_from_conversation_only:
            self.needs_conversation_memory = True
            self.needs_knowledge_index = False
        return self


MAX_CHAT_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)
    channel: str = Field(default="local", max_length=64)
    teams_conversation_id: str | None = Field(default=None, max_length=256)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value):
        # Quita caracteres invisibles/bidi usados para esconder instrucciones (ASCII smuggling).
        from internal_assistant.security.prompt_guard import normalize_untrusted_text

        return normalize_untrusted_text(value).strip() if isinstance(value, str) else value


class ChatResponse(BaseModel):
    conversation_id: int
    answer: str
    sources: list[SourceSnippet] = Field(default_factory=list)
    related_incidents: list[dict] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_attempt: int = 0
    should_offer_incident: bool = False
    adaptive_card: dict | None = None
    fallback_text: str
    created_ticket_id: int | None = None
    created_ticket_external_id: str | None = None
    message_id: int | None = None
    # Identificador de la peticion: localiza su traza extendida y su traza en LangSmith.
    request_id: str | None = None
    # Comportamiento final del asistente (answer_with_sources, ask_clarification, reject_prompt_injection...).
    behavior: str | None = None
