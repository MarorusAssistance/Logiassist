from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from internal_assistant.llm import anthropic_foundry_provider as module
from internal_assistant.llm.anthropic_foundry_provider import AnthropicFoundryProvider, _split_messages
from internal_assistant.observability.usage import metering


def _settings(**overrides):
    values = dict(
        anthropic_foundry_api_key="test-key",
        anthropic_foundry_resource="res",
        anthropic_foundry_base_url="",
        llm_timeout_seconds=30.0,
        llm_effort="low",
        chat_model="claude-haiku-5-5",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _message(text: str, *, stop_reason: str = "end_turn", input_tokens=100, output_tokens=20, cache_read=400, cache_write=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class FakeStream:
    def __init__(self, message, deltas):
        self.message = message
        self.text_stream = iter(deltas)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeMessages:
    def __init__(self, message, deltas=None):
        self.message = message
        self.deltas = deltas or []
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.message

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.message, self.deltas)


def build_provider(monkeypatch, message, deltas=None):
    monkeypatch.setattr(module, "get_settings", lambda: _settings())
    monkeypatch.setattr(module, "AnthropicFoundry", lambda **kwargs: SimpleNamespace(messages=FakeMessages(message, deltas)))
    return AnthropicFoundryProvider()


DECISION = {
    "answer": "Solicita el acceso en SafeGate.",
    "needs_clarification": False,
    "clarification_question": None,
    "should_offer_incident": False,
    "used_chunk_ids": [1],
}


def test_split_messages_caches_system_prompt():
    system, conversation = _split_messages([{"role": "system", "content": "reglas"}, {"role": "user", "content": "hola"}])
    assert system == [{"type": "text", "text": "reglas", "cache_control": {"type": "ephemeral"}}]
    assert conversation == [{"role": "user", "content": "hola"}]


def test_generate_uses_structured_output_effort_and_no_temperature(monkeypatch):
    provider = build_provider(monkeypatch, _message(json.dumps(DECISION)))

    with metering() as meter:
        decision = provider.generate_chat_response(
            question="Como pido acceso?",
            context_chunks=[{"chunk_id": 1, "content": "SafeGate", "source_type": "document", "source_id": 1}],
            conversation_state={},
        )

    call = provider.client.messages.calls[0]
    assert decision.answer == DECISION["answer"]
    assert call["model"] == "claude-haiku-5-5"
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call
    assert all(item["role"] != "system" for item in call["messages"])
    event = meter.events[0]
    assert (event.purpose, event.input_tokens, event.cached_input_tokens, event.output_tokens) == ("answer", 500, 400, 20)


def test_refusal_returns_safe_answer(monkeypatch):
    provider = build_provider(monkeypatch, _message("", stop_reason="refusal"))
    decision = provider.generate_chat_response(question="x", context_chunks=[], conversation_state={})
    assert decision.answer.startswith("No puedo")
    assert decision.used_chunk_ids == []


def test_stream_emits_only_answer_text(monkeypatch):
    raw = json.dumps(DECISION)
    provider = build_provider(monkeypatch, _message(raw), deltas=[raw[:20], raw[20:45], raw[45:]])

    with metering() as meter:
        events = list(provider.stream_chat_response(question="x", context_chunks=[], conversation_state={}))

    tokens = "".join(event.text for event in events if event.kind == "token")
    assert tokens == DECISION["answer"]
    assert events[-1].kind == "final" and events[-1].decision.used_chunk_ids == [1]
    assert len(meter.events) == 1


def test_planner_parses_schema_output(monkeypatch):
    plan = {
        "intent": "question_answering",
        "needs_conversation_memory": False,
        "needs_knowledge_index": True,
        "can_answer_from_conversation_only": False,
        "should_ask_clarification_first": False,
        "conversation_memory_query": "",
        "knowledge_index_query": "acceso temporal SafeGate",
        "user_context_summary": "",
        "expected_source_preference": ["document"],
        "mentioned_systems": ["SafeGate"],
        "retrieval_filters": {
            "source_types": ["document"],
            "affected_systems": ["SafeGate"],
            "departments": [],
            "document_types": [],
            "incident_statuses": [],
            "is_resolved": None,
            "tags_any": [],
        },
        "filter_reason": "",
        "reason": "",
    }
    provider = build_provider(monkeypatch, _message(json.dumps(plan)))
    result = provider.plan_chat(message="Como pido acceso temporal en SafeGate?", recent_messages=[], conversation_state={})
    assert result.retrieval_filters.affected_systems == ["SafeGate"]


def test_requires_exactly_one_endpoint_setting(monkeypatch):
    monkeypatch.setattr(module, "get_settings", lambda: _settings(anthropic_foundry_base_url="https://x/anthropic/"))
    with pytest.raises(ValueError):
        AnthropicFoundryProvider()
