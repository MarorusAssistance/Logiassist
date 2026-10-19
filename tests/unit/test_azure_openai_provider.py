from __future__ import annotations

from types import SimpleNamespace

import pytest

from internal_assistant.llm import azure_openai_provider as module
from internal_assistant.llm.azure_openai_provider import AzureOpenAIProvider, foundry_openai_base_url


def _settings(**overrides):
    values = dict(azure_ai_api_key="key", azure_ai_resource="logiassist-ai-swc", azure_ai_base_url="", llm_timeout_seconds=30.0)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_base_url_is_built_from_resource_or_override():
    assert foundry_openai_base_url(_settings()) == "https://logiassist-ai-swc.services.ai.azure.com/openai/v1/"
    assert foundry_openai_base_url(_settings(azure_ai_base_url="https://x.example/openai/v1")) == "https://x.example/openai/v1/"


def test_provider_uses_foundry_client_and_billable_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(module, "get_settings", lambda: _settings())
    monkeypatch.setattr(module, "OpenAI", lambda **kwargs: captured.update(kwargs) or SimpleNamespace())

    provider = AzureOpenAIProvider()

    assert provider.provider_name == "azure_foundry"
    assert captured["base_url"].endswith(".services.ai.azure.com/openai/v1/")


def test_provider_requires_key(monkeypatch):
    monkeypatch.setattr(module, "get_settings", lambda: _settings(azure_ai_api_key=""))
    with pytest.raises(ValueError):
        AzureOpenAIProvider()


def _content_filter_error():
    import httpx
    from openai import BadRequestError

    request = httpx.Request("POST", "https://x/openai/v1/chat/completions")
    body = {"error": {"code": "content_filter", "message": "Response content blocked by label 'Jailbreak'."}}
    return BadRequestError("blocked", response=httpx.Response(400, request=request, json=body), body=body)


def test_content_filter_becomes_controlled_refusal(monkeypatch):
    def create(**kwargs):
        raise _content_filter_error()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(module, "get_settings", lambda: _settings(chat_model="DeepSeek-V4-Flash"))
    monkeypatch.setattr(module, "OpenAI", lambda **kwargs: client)
    provider = AzureOpenAIProvider()

    decision = provider.generate_chat_response(question="x", context_chunks=[], conversation_state={})
    events = list(provider.stream_chat_response(question="x", context_chunks=[], conversation_state={}))
    plan = provider.plan_chat(message="hola", recent_messages=[], conversation_state={})

    assert decision.answer.startswith("No puedo")
    assert events[-1].kind == "final" and events[-1].decision.answer.startswith("No puedo")
    assert plan.knowledge_index_query or plan.needs_knowledge_index
