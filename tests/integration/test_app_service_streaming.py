from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from internal_assistant.schemas.chat import ChatResponse, SourceSnippet


@contextmanager
def _fake_session_scope():
    yield object()


class FakeStreamingChatService:
    def __init__(self, _session):
        pass

    def handle_chat(self, payload, *, stream_token_callback=None):
        if stream_token_callback:
            stream_token_callback("Respuesta ")
            stream_token_callback("en streaming.")
        return ChatResponse(
            conversation_id=42,
            message_id=7,
            answer="Respuesta en streaming.",
            sources=[
                SourceSnippet(
                    source_type="document",
                    source_id=1,
                    title="Procedimiento demo",
                    excerpt="Fragmento",
                    chunk_id=10,
                )
            ],
            related_incidents=[],
            needs_clarification=False,
            clarification_attempt=0,
            should_offer_incident=False,
            adaptive_card=None,
            fallback_text="Respuesta en streaming.",
        )


def test_api_chat_stream_returns_token_sources_and_final_events(app_paths, load_module, monkeypatch):
    module = load_module("app_service_main_stream", app_paths["app_service"])
    monkeypatch.setattr(module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(module, "ChatService", FakeStreamingChatService)
    client = TestClient(module.app)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"user_id": "u1", "message": "hola", "channel": "web-demo"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert "event: token" in body
    assert "Respuesta " in body
    assert "event: sources" in body
    assert "event: final" in body
    assert '"conversation_id": 42' in body
