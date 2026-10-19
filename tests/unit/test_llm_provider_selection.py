from __future__ import annotations

from types import SimpleNamespace

import pytest

from internal_assistant.llm.openai_provider import build_default_provider


def make_settings(**overrides):
    defaults = {
        "llm_provider": "auto",
        "llm_base_url": "",
        "embeddings_provider": "",
        "openai_api_key": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_default_provider_uses_mock_when_auto_has_no_real_config(monkeypatch):
    monkeypatch.setattr("internal_assistant.llm.openai_provider.get_settings", lambda: make_settings())
    monkeypatch.setattr("internal_assistant.llm.openai_provider.MockLLMProvider", lambda: "mock")

    assert build_default_provider() == "mock"


def test_build_default_provider_ignores_placeholder_openai_key(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(openai_api_key="<your-openai-api-key>"),
    )
    monkeypatch.setattr("internal_assistant.llm.openai_provider.MockLLMProvider", lambda: "mock")

    assert build_default_provider() == "mock"


def test_build_default_provider_uses_openai_when_real_key_exists(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(openai_api_key="sk-real-key"),
    )
    monkeypatch.setattr("internal_assistant.llm.openai_provider.OpenAIProvider", lambda: "openai")

    assert build_default_provider() == "openai"


def test_build_default_provider_uses_openai_compatible_when_base_url_exists(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(llm_base_url="http://localhost:11434/v1"),
    )
    monkeypatch.setattr("internal_assistant.llm.openai_provider.OpenAICompatibleProvider", lambda: "local")

    assert build_default_provider() == "local"


def test_build_default_provider_supports_openai_embeddings_with_local_chat(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(
            llm_provider="openai_compatible",
            embeddings_provider="openai",
            llm_base_url="http://localhost:1234/v1",
            openai_api_key="sk-real-key",
        ),
    )
    monkeypatch.setattr("internal_assistant.llm.openai_provider.OpenAIProvider", lambda: "openai-embeddings")
    monkeypatch.setattr("internal_assistant.llm.openai_provider.OpenAICompatibleProvider", lambda: "local-chat")

    provider = build_default_provider()

    assert provider.embeddings_provider == "openai-embeddings"
    assert provider.chat_provider == "local-chat"


def test_build_default_provider_rejects_unsupported_mixed_providers(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(llm_provider="openai_compatible", embeddings_provider="mock", llm_base_url="http://local/v1"),
    )

    with pytest.raises(ValueError, match="EMBEDDINGS_PROVIDER"):
        build_default_provider()


def test_build_default_provider_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.openai_provider.get_settings",
        lambda: make_settings(llm_provider="unsupported"),
    )

    with pytest.raises(ValueError, match="LLM_PROVIDER no soportado"):
        build_default_provider()
