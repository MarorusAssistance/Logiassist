from __future__ import annotations

from collections.abc import Iterator
import time

from openai import BadRequestError, OpenAI

from internal_assistant.config import get_settings
from internal_assistant.llm.base import LLMProvider
from internal_assistant.llm.common import (
    CONTENT_FILTER_ANSWER,
    is_content_filter_error,
    build_chat_messages,
    build_planner_messages,
    parse_assistant_decision,
    parse_chat_plan,
    validate_embedding_dimensions,
)
from internal_assistant.llm.streaming import ChatStreamEvent, JsonAnswerStreamExtractor
from internal_assistant.llm.anthropic_foundry_provider import AnthropicFoundryProvider
from internal_assistant.llm.mock_provider import MockLLMProvider
from internal_assistant.llm.openai_compatible_provider import OpenAICompatibleProvider
from internal_assistant.observability.tracing import set_span_attributes, start_span
from internal_assistant.observability.trace_store import record_llm_call
from internal_assistant.observability.usage import record_chat_usage, record_embedding_usage
from internal_assistant.schemas.chat import AssistantDecision, ChatPlan


EMBEDDING_BATCH_SIZE = 32


class OpenAIProvider(LLMProvider):
    provider_name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key, max_retries=0, timeout=settings.llm_timeout_seconds)

    def _reasoning_params(self) -> dict:
        """Modelos de razonamiento (gpt-5.x, o-series): LLM_REASONING_EFFORT=none los deja sin razonar y rapidos."""
        effort = str(getattr(self.settings, "llm_reasoning_effort", "") or "").strip()
        return {"reasoning_effort": effort} if effort else {}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        with start_span(
            "llm.embeddings",
            {
                "llm.provider": "openai",
                "llm.embedding_model": self.settings.embedding_model,
                "llm.embedding_dimensions": self.settings.embedding_dimensions,
                "llm.embedding_input_count": len(texts),
            },
        ) as span:
            embeddings: list[list[float]] = []
            for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
                batch = texts[start : start + EMBEDDING_BATCH_SIZE]
                response = self.client.embeddings.create(
                    model=self.settings.embedding_model,
                    input=batch,
                    dimensions=self.settings.embedding_dimensions,
                )
                record_embedding_usage(
                    getattr(response, "usage", None),
                    provider=self.provider_name,
                    model=self.settings.embedding_model,
                )
                embeddings.extend(item.embedding for item in response.data)
            validated = validate_embedding_dimensions(embeddings, self.settings.embedding_dimensions)
            set_span_attributes(span, {"llm.embedding_output_count": len(validated)})
            return validated

    def _generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        with start_span(
            "llm.chat_completion",
            {
                "llm.provider": "openai",
                "llm.chat_model": self.settings.chat_model,
                "llm.context_chunk_count": len(context_chunks),
                "question.length": len(question),
                "llm.timeout_seconds": self.settings.llm_timeout_seconds,
            },
        ) as span:
            messages = build_chat_messages(
                question=question,
                context_chunks=context_chunks,
                conversation_state=conversation_state,
            )
            started = time.perf_counter()
            response = self.client.chat.completions.create(
                model=self.settings.chat_model,
                temperature=0.1,
                **self._reasoning_params(),
                response_format={"type": "json_object"},
                messages=messages,
            )
            record_chat_usage(
                getattr(response, "usage", None),
                provider=self.provider_name,
                model=self.settings.chat_model,
                purpose="answer",
            )
            record_llm_call(
                purpose="answer",
                provider=self.provider_name,
                model=self.settings.chat_model,
                messages=messages,
                raw_output=response.choices[0].message.content,
                usage=getattr(response, "usage", None),
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                finish_reason=getattr(response.choices[0], "finish_reason", None),
            )
            decision = parse_assistant_decision(response.choices[0].message.content)
            set_span_attributes(
                span,
                {
                    "llm.needs_clarification": decision.needs_clarification,
                    "llm.should_offer_incident": decision.should_offer_incident,
                    "llm.used_chunk_ids": decision.used_chunk_ids,
                    "llm.answer_length": len(decision.answer or ""),
                },
            )
            return decision

    def _stream_chat_response(
        self,
        *,
        question: str,
        context_chunks: list[dict],
        conversation_state: dict,
    ) -> Iterator[ChatStreamEvent]:
        with start_span(
            "llm.chat_completion.stream",
            {
                "llm.provider": "openai",
                "llm.chat_model": self.settings.chat_model,
                "llm.context_chunk_count": len(context_chunks),
                "question.length": len(question),
                "llm.timeout_seconds": self.settings.llm_timeout_seconds,
            },
        ) as span:
            extractor = JsonAnswerStreamExtractor()
            messages = build_chat_messages(
                question=question,
                context_chunks=context_chunks,
                conversation_state=conversation_state,
            )
            started = time.perf_counter()
            stream = self.client.chat.completions.create(
                model=self.settings.chat_model,
                temperature=0.1,
                **self._reasoning_params(),
                response_format={"type": "json_object"},
                stream=True,
                stream_options={"include_usage": True},
                messages=messages,
            )
            stream_usage = None
            for chunk in stream:
                # Con include_usage, el ultimo chunk trae `usage` y `choices` vacio.
                if getattr(chunk, "usage", None) is not None:
                    stream_usage = chunk.usage
                delta = chunk.choices[0].delta.content if chunk.choices else None
                for token in extractor.feed(delta or ""):
                    yield ChatStreamEvent(kind="token", text=token)

            if stream_usage is not None:
                record_chat_usage(
                    stream_usage,
                    provider=self.provider_name,
                    model=self.settings.chat_model,
                    purpose="answer",
                )
            record_llm_call(
                purpose="answer",
                provider=self.provider_name,
                model=self.settings.chat_model,
                messages=messages,
                raw_output=extractor.raw_json,
                usage=stream_usage,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            decision = parse_assistant_decision(extractor.raw_json)
            set_span_attributes(
                span,
                {
                    "llm.needs_clarification": decision.needs_clarification,
                    "llm.should_offer_incident": decision.should_offer_incident,
                    "llm.used_chunk_ids": decision.used_chunk_ids,
                    "llm.answer_length": len(decision.answer or ""),
                    "llm.streamed": True,
                },
            )
            yield ChatStreamEvent(kind="final", decision=decision)

    def _plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        with start_span(
            "llm.chat_planner",
            {
                "llm.provider": "openai",
                "llm.chat_model": self.settings.chat_model,
                "planner.recent_message_count": len(recent_messages),
                "message.length": len(message),
                "llm.timeout_seconds": self.settings.llm_timeout_seconds,
            },
        ) as span:
            messages = build_planner_messages(
                message=message,
                recent_messages=recent_messages,
                conversation_state=conversation_state,
            )
            started = time.perf_counter()
            response = self.client.chat.completions.create(
                model=self.settings.chat_model,
                temperature=0.0,
                **self._reasoning_params(),
                response_format={"type": "json_object"},
                messages=messages,
            )
            record_chat_usage(
                getattr(response, "usage", None),
                provider=self.provider_name,
                model=self.settings.chat_model,
                purpose="planner",
            )
            record_llm_call(
                purpose="planner",
                provider=self.provider_name,
                model=self.settings.chat_model,
                messages=messages,
                raw_output=response.choices[0].message.content,
                usage=getattr(response, "usage", None),
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                finish_reason=getattr(response.choices[0], "finish_reason", None),
            )
            plan = parse_chat_plan(response.choices[0].message.content, fallback_message=message)
            set_span_attributes(
                span,
                {
                    "planner.needs_conversation_memory": plan.needs_conversation_memory,
                    "planner.needs_knowledge_index": plan.needs_knowledge_index,
                    "planner.can_answer_from_conversation_only": plan.can_answer_from_conversation_only,
                    "planner.should_ask_clarification_first": plan.should_ask_clarification_first,
                    "planner.retrieval_filters": plan.retrieval_filters.to_dict(),
                },
            )
            return plan

    # Azure (Prompt Shields / filtros de contenido) puede bloquear peticiones o respuestas con un 400.
    # Es otra capa de defensa: se responde con un rechazo controlado en lugar de un error 500.
    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        try:
            return self._generate_chat_response(
                question=question, context_chunks=context_chunks, conversation_state=conversation_state
            )
        except BadRequestError as exc:
            if not is_content_filter_error(exc):
                raise
            return AssistantDecision(answer=CONTENT_FILTER_ANSWER)

    def stream_chat_response(
        self,
        *,
        question: str,
        context_chunks: list[dict],
        conversation_state: dict,
    ) -> Iterator[ChatStreamEvent]:
        try:
            yield from self._stream_chat_response(
                question=question, context_chunks=context_chunks, conversation_state=conversation_state
            )
        except BadRequestError as exc:
            if not is_content_filter_error(exc):
                raise
            yield ChatStreamEvent(kind="final", decision=AssistantDecision(answer=CONTENT_FILTER_ANSWER))

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        try:
            return self._plan_chat(message=message, recent_messages=recent_messages, conversation_state=conversation_state)
        except BadRequestError as exc:
            if not is_content_filter_error(exc):
                raise
            return ChatPlan.fallback(message)


class SplitLLMProvider(LLMProvider):
    def __init__(self, *, embeddings_provider: LLMProvider, chat_provider: LLMProvider) -> None:
        self.embeddings_provider = embeddings_provider
        self.chat_provider = chat_provider

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.embeddings_provider.embed_texts(texts)

    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        return self.chat_provider.generate_chat_response(
            question=question,
            context_chunks=context_chunks,
            conversation_state=conversation_state,
        )

    def stream_chat_response(
        self,
        *,
        question: str,
        context_chunks: list[dict],
        conversation_state: dict,
    ) -> Iterator[ChatStreamEvent]:
        yield from self.chat_provider.stream_chat_response(
            question=question,
            context_chunks=context_chunks,
            conversation_state=conversation_state,
        )

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        return self.chat_provider.plan_chat(
            message=message,
            recent_messages=recent_messages,
            conversation_state=conversation_state,
        )


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"", "<your-openai-api-key>", "your-openai-api-key", "change-me", "set-me"}


def normalize_provider_name(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "openai-compatible": "openai_compatible",
        "local": "openai_compatible",
        "anthropic-foundry": "anthropic_foundry",
        "foundry": "anthropic_foundry",
        "azure": "azure_foundry",
        "azure_openai": "azure_foundry",
        "azure-foundry": "azure_foundry",
    }
    return aliases.get(normalized, normalized)


def resolve_provider_name(settings=None) -> str:
    current_settings = settings or get_settings()
    provider = normalize_provider_name(current_settings.llm_provider or "auto")
    if provider in {"", "auto"}:
        if current_settings.llm_base_url:
            return "openai_compatible"
        if not _is_placeholder(current_settings.openai_api_key):
            return "openai"
        return "mock"
    return provider


def build_default_provider() -> LLMProvider:
    settings = get_settings()
    provider = resolve_provider_name(settings)
    embeddings_provider = normalize_provider_name(settings.embeddings_provider or "")
    if embeddings_provider in {"", "auto"}:
        embeddings_provider = provider

    if provider == "openai_compatible" and embeddings_provider == "openai":
        return SplitLLMProvider(
            embeddings_provider=OpenAIProvider(),
            chat_provider=OpenAICompatibleProvider(),
        )
    if provider == "anthropic_foundry":
        # Claude no tiene embeddings: siempre se combinan con los de OpenAI.
        if embeddings_provider not in {"openai", "anthropic_foundry"}:
            raise ValueError("LLM_PROVIDER=anthropic_foundry requiere EMBEDDINGS_PROVIDER=openai")
        return SplitLLMProvider(
            embeddings_provider=OpenAIProvider(),
            chat_provider=AnthropicFoundryProvider(),
        )
    if embeddings_provider != provider:
        raise ValueError(
            "EMBEDDINGS_PROVIDER debe coincidir con LLM_PROVIDER, usar auto, o una combinacion soportada: "
            "LLM_PROVIDER=openai_compatible|anthropic_foundry + EMBEDDINGS_PROVIDER=openai"
        )

    if provider == "azure_foundry":
        # Import diferido: AzureOpenAIProvider hereda de OpenAIProvider (este modulo).
        from internal_assistant.llm.azure_openai_provider import AzureOpenAIProvider

        return AzureOpenAIProvider()
    if provider == "mock":
        return MockLLMProvider()
    if provider == "openai":
        return OpenAIProvider()
    if provider == "openai_compatible":
        return OpenAICompatibleProvider()
    raise ValueError(f"LLM_PROVIDER no soportado: {settings.llm_provider}")
