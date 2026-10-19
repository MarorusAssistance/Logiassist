from __future__ import annotations

from types import SimpleNamespace

from internal_assistant.llm.mock_provider import MockLLMProvider
from internal_assistant.llm.openai_provider import SplitLLMProvider
from internal_assistant.llm.streaming import JsonAnswerStreamExtractor
from internal_assistant.schemas.chat import AssistantDecision


def test_json_answer_stream_extractor_emits_only_answer_text():
    extractor = JsonAnswerStreamExtractor()
    emitted = []
    for chunk in ['{"answer":"Hola', ' mundo\\n', 'sin JSON","needs_clarification":false}']:
        emitted.extend(extractor.feed(chunk))

    assert "".join(emitted) == "Hola mundo\nsin JSON"
    assert "needs_clarification" not in "".join(emitted)
    assert "{" not in "".join(emitted)


def test_mock_provider_streams_tokens_and_final(monkeypatch):
    monkeypatch.setattr(
        "internal_assistant.llm.mock_provider.get_settings",
        lambda: SimpleNamespace(),
    )
    provider = MockLLMProvider()
    events = list(
        provider.stream_chat_response(
            question="Como cierro una expedicion?",
            context_chunks=[
                {
                    "chunk_id": 1,
                    "content": "Cierra picking y packing antes de confirmar expedicion.",
                    "source_type": "document",
                    "source_id": 1,
                    "metadata": {},
                }
            ],
            conversation_state={},
        )
    )

    assert [event.kind for event in events][0] == "token"
    assert events[-1].kind == "final"
    assert events[-1].decision is not None
    assert events[-1].decision.used_chunk_ids == [1]


def test_split_provider_delegates_streaming_to_chat_provider():
    class EmbeddingsProvider:
        def embed_texts(self, texts):
            return [[0.1] for _ in texts]

    class ChatProvider:
        def __init__(self):
            self.called = False

        def stream_chat_response(self, *, question, context_chunks, conversation_state):
            self.called = True
            yield from [
                SimpleNamespace(kind="token", text="respuesta", decision=None),
                SimpleNamespace(
                    kind="final",
                    text="",
                    decision=AssistantDecision(answer="respuesta", used_chunk_ids=[]),
                ),
            ]

        def generate_chat_response(self, *, question, context_chunks, conversation_state):
            return AssistantDecision(answer="respuesta")

        def plan_chat(self, *, message, recent_messages, conversation_state):
            raise AssertionError("not needed")

    chat_provider = ChatProvider()
    provider = SplitLLMProvider(embeddings_provider=EmbeddingsProvider(), chat_provider=chat_provider)

    events = list(provider.stream_chat_response(question="q", context_chunks=[], conversation_state={}))

    assert chat_provider.called is True
    assert events[0].text == "respuesta"
    assert events[-1].decision.answer == "respuesta"
