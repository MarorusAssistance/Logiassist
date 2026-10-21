"""Claude (p. ej. claude-haiku-5-5) servido desde Microsoft Foundry con el SDK oficial de Anthropic.

Diferencias frente al provider de OpenAI:
- Sin `temperature`: Haiku 5.5 rechaza valores de muestreo no por defecto.
- JSON garantizado con structured outputs (`output_config.format`) en lugar de `response_format`.
- Razonamiento adaptativo controlado con `output_config.effort` (LLM_EFFORT, por defecto `low`).
- El system prompt se cachea (prompt caching) porque es identico en todas las llamadas del proceso.
- No hay embeddings: se combinan con OpenAI mediante SplitLLMProvider.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from anthropic import AnthropicFoundry

from internal_assistant.config import get_settings
from internal_assistant.llm.base import LLMProvider
from internal_assistant.llm.common import (
    build_chat_messages,
    build_planner_messages,
    parse_assistant_decision,
    parse_chat_plan,
)
from internal_assistant.llm.streaming import ChatStreamEvent, JsonAnswerStreamExtractor
from internal_assistant.observability.tracing import set_span_attributes, start_span
from internal_assistant.observability.trace_store import record_llm_call
from internal_assistant.observability.usage import record_usage
from internal_assistant.schemas.chat import AssistantDecision, ChatPlan


ANSWER_MAX_TOKENS = 8000
PLANNER_MAX_TOKENS = 4000
REFUSAL_ANSWER = "No puedo ayudar con esa peticion. Si tienes una duda operativa concreta, indica el sistema y el proceso."

_STRING_LIST = {"type": "array", "items": {"type": "string"}}

ASSISTANT_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "should_offer_incident": {"type": "boolean"},
        "used_chunk_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["answer", "needs_clarification", "clarification_question", "should_offer_incident", "used_chunk_ids"],
    "additionalProperties": False,
}

CHAT_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "out_of_scope": {"type": "boolean"},
        "needs_conversation_memory": {"type": "boolean"},
        "needs_knowledge_index": {"type": "boolean"},
        "can_answer_from_conversation_only": {"type": "boolean"},
        "should_ask_clarification_first": {"type": "boolean"},
        "conversation_memory_query": {"type": "string"},
        "knowledge_index_query": {"type": "string"},
        "user_context_summary": {"type": "string"},
        "expected_source_preference": _STRING_LIST,
        "mentioned_systems": _STRING_LIST,
        "search_keywords": _STRING_LIST,
        "retrieval_filters": {
            "type": "object",
            "properties": {
                "source_types": _STRING_LIST,
                "affected_systems": _STRING_LIST,
                "departments": _STRING_LIST,
                "document_types": _STRING_LIST,
                "incident_statuses": _STRING_LIST,
                "is_resolved": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
                "tags_any": _STRING_LIST,
            },
            "required": [
                "source_types",
                "affected_systems",
                "departments",
                "document_types",
                "incident_statuses",
                "is_resolved",
                "tags_any",
            ],
            "additionalProperties": False,
        },
        "filter_reason": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "intent",
        "out_of_scope",
        "needs_conversation_memory",
        "needs_knowledge_index",
        "can_answer_from_conversation_only",
        "should_ask_clarification_first",
        "conversation_memory_query",
        "knowledge_index_query",
        "user_context_summary",
        "expected_source_preference",
        "mentioned_systems",
        "search_keywords",
        "retrieval_filters",
        "filter_reason",
        "reason",
    ],
    "additionalProperties": False,
}


def _split_messages(messages: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Separa el mensaje de sistema (cacheado) del resto, en formato Messages API."""
    system_text = "\n\n".join(item["content"] for item in messages if item["role"] == "system")
    conversation = [{"role": item["role"], "content": item["content"]} for item in messages if item["role"] != "system"]
    system = [{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}] if system_text else []
    return system, conversation


def _response_text(message: Any) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


class AnthropicFoundryProvider(LLMProvider):
    provider_name = "anthropic_foundry"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_foundry_api_key:
            raise ValueError("ANTHROPIC_FOUNDRY_API_KEY es obligatorio cuando LLM_PROVIDER=anthropic_foundry")
        if bool(settings.anthropic_foundry_resource) == bool(settings.anthropic_foundry_base_url):
            raise ValueError("Define ANTHROPIC_FOUNDRY_RESOURCE o ANTHROPIC_FOUNDRY_BASE_URL (solo uno de los dos)")
        self.settings = settings
        self.client = AnthropicFoundry(
            api_key=settings.anthropic_foundry_api_key,
            resource=settings.anthropic_foundry_resource or None,
            base_url=settings.anthropic_foundry_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Claude no ofrece embeddings: usa EMBEDDINGS_PROVIDER=openai")

    def _output_config(self, schema: dict[str, Any]) -> dict[str, Any]:
        return {"effort": self.settings.llm_effort, "format": {"type": "json_schema", "schema": schema}}

    def _record(self, message: Any, purpose: str) -> None:
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        record_usage(
            component="chat",
            purpose=purpose,
            provider=self.provider_name,
            model=self.settings.chat_model,
            # Mismo criterio que OpenAI: input total incluye la parte cacheada.
            input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
            cached_input_tokens=cache_read,
            output_tokens=usage.output_tokens or 0,
        )

    def complete_json(self, *, messages: list[dict[str, str]], schema: dict[str, Any], purpose: str, max_tokens: int) -> tuple[str, str]:
        """Llamada generica con salida JSON validada. Devuelve (texto, stop_reason)."""
        system, conversation = _split_messages(messages)
        started = time.perf_counter()
        message = self.client.messages.create(
            model=self.settings.chat_model,
            max_tokens=max_tokens,
            system=system,
            messages=conversation,
            output_config=self._output_config(schema),
        )
        self._record(message, purpose)
        text = _response_text(message)
        record_llm_call(
            purpose=purpose,
            provider=self.provider_name,
            model=self.settings.chat_model,
            messages=messages,
            raw_output=text,
            usage=getattr(message, "usage", None),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            finish_reason=message.stop_reason,
        )
        return text, message.stop_reason or ""

    def generate_chat_response(self, *, question: str, context_chunks: list[dict], conversation_state: dict) -> AssistantDecision:
        with start_span(
            "llm.chat_completion",
            {
                "llm.provider": self.provider_name,
                "llm.chat_model": self.settings.chat_model,
                "llm.effort": self.settings.llm_effort,
                "llm.context_chunk_count": len(context_chunks),
                "question.length": len(question),
            },
        ) as span:
            text, stop_reason = self.complete_json(
                messages=build_chat_messages(
                    question=question,
                    context_chunks=context_chunks,
                    conversation_state=conversation_state,
                ),
                schema=ASSISTANT_DECISION_SCHEMA,
                purpose="answer",
                max_tokens=ANSWER_MAX_TOKENS,
            )
            set_span_attributes(span, {"llm.stop_reason": stop_reason})
            if stop_reason == "refusal":
                return AssistantDecision(answer=REFUSAL_ANSWER)
            return parse_assistant_decision(text)

    def stream_chat_response(
        self,
        *,
        question: str,
        context_chunks: list[dict],
        conversation_state: dict,
    ) -> Iterator[ChatStreamEvent]:
        system, conversation = _split_messages(
            build_chat_messages(
                question=question,
                context_chunks=context_chunks,
                conversation_state=conversation_state,
            )
        )
        with start_span(
            "llm.chat_completion.stream",
            {
                "llm.provider": self.provider_name,
                "llm.chat_model": self.settings.chat_model,
                "llm.effort": self.settings.llm_effort,
                "llm.context_chunk_count": len(context_chunks),
                "question.length": len(question),
            },
        ) as span:
            extractor = JsonAnswerStreamExtractor()
            with self.client.messages.stream(
                model=self.settings.chat_model,
                max_tokens=ANSWER_MAX_TOKENS,
                system=system,
                messages=conversation,
                output_config=self._output_config(ASSISTANT_DECISION_SCHEMA),
            ) as stream:
                # text_stream solo emite texto; los bloques de thinking no llegan aqui.
                for delta in stream.text_stream:
                    for token in extractor.feed(delta):
                        yield ChatStreamEvent(kind="token", text=token)
                final_message = stream.get_final_message()
            self._record(final_message, "answer")
            record_llm_call(
                purpose="answer",
                provider=self.provider_name,
                model=self.settings.chat_model,
                messages=build_chat_messages(
                    question=question,
                    context_chunks=context_chunks,
                    conversation_state=conversation_state,
                ),
                raw_output=extractor.raw_json,
                usage=getattr(final_message, "usage", None),
                finish_reason=final_message.stop_reason,
            )
            set_span_attributes(span, {"llm.stop_reason": final_message.stop_reason or "", "llm.streamed": True})
            if final_message.stop_reason == "refusal":
                decision = AssistantDecision(answer=REFUSAL_ANSWER)
            else:
                decision = parse_assistant_decision(extractor.raw_json)
            yield ChatStreamEvent(kind="final", decision=decision)

    def plan_chat(self, *, message: str, recent_messages: list[dict], conversation_state: dict) -> ChatPlan:
        with start_span(
            "llm.chat_planner",
            {
                "llm.provider": self.provider_name,
                "llm.chat_model": self.settings.chat_model,
                "planner.recent_message_count": len(recent_messages),
                "message.length": len(message),
            },
        ) as span:
            text, stop_reason = self.complete_json(
                messages=build_planner_messages(
                    message=message,
                    recent_messages=recent_messages,
                    conversation_state=conversation_state,
                ),
                schema=CHAT_PLAN_SCHEMA,
                purpose="planner",
                max_tokens=PLANNER_MAX_TOKENS,
            )
            if stop_reason == "refusal":
                return ChatPlan.fallback(message)
            plan = parse_chat_plan(text, fallback_message=message)
            set_span_attributes(
                span,
                {
                    "planner.needs_conversation_memory": plan.needs_conversation_memory,
                    "planner.needs_knowledge_index": plan.needs_knowledge_index,
                    "planner.should_ask_clarification_first": plan.should_ask_clarification_first,
                },
            )
            return plan


def judge_payload(provider: AnthropicFoundryProvider, messages: list[dict[str, str]]) -> dict[str, Any]:
    text, _ = provider.complete_json(
        messages=messages,
        schema={
            "type": "object",
            "properties": {
                "llm_score": {"type": "number"},
                "llm_verdict": {"type": "string", "enum": ["pass", "fail"]},
                "llm_reason": {"type": "string"},
            },
            "required": ["llm_score", "llm_verdict", "llm_reason"],
            "additionalProperties": False,
        },
        purpose="judge",
        max_tokens=2000,
    )
    return json.loads(text) if text else {}
