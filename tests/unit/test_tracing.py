from __future__ import annotations

from types import SimpleNamespace

from internal_assistant.observability.tracing import (
    RagTrace,
    retrieval_span_attributes,
    safe_span_attributes,
    serialize_chunks_for_langsmith,
)
from internal_assistant.rag.retrieval import RetrievedChunk


class FakeLangSmithClient:
    def __init__(self):
        self.created = []
        self.updated = []

    def create_run(self, **kwargs):
        self.created.append(kwargs)

    def update_run(self, run_id, **kwargs):
        self.updated.append({"run_id": run_id, **kwargs})


def enabled_settings():
    return SimpleNamespace(
        langsmith_tracing=True,
        langsmith_api_key="test-key",
        langsmith_project="test-project",
        langsmith_endpoint="https://example.langsmith.test",
    )


def disabled_settings():
    return SimpleNamespace(
        langsmith_tracing=False,
        langsmith_api_key="",
        langsmith_project="test-project",
        langsmith_endpoint="https://example.langsmith.test",
    )


def sample_chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=42,
        source_type="document",
        source_id=7,
        content="Contenido privado del chunk para depurar en LangSmith.",
        metadata={"title": "Procedimiento SafeGate", "source_url": "https://example.test/doc"},
        vector_score=0.8,
        text_score=0.2,
        final_score=0.74,
    )


def test_langsmith_tracing_disabled_does_not_create_runs():
    client = FakeLangSmithClient()
    trace = RagTrace(settings=disabled_settings(), client=client)

    with trace.start_run("rag.chat", inputs={"question": "texto de usuario"}) as run:
        run.set_outputs({"answer": "respuesta"})

    assert client.created == []
    assert client.updated == []


def test_langsmith_fake_client_receives_root_and_child_runs_with_content():
    client = FakeLangSmithClient()
    trace = RagTrace(settings=enabled_settings(), client=client)
    chunks = serialize_chunks_for_langsmith([sample_chunk()])

    with trace.start_run("rag.chat", inputs={"question": "Como uso SafeGate?"}) as root:
        with root.child("rag.retrieve", run_type="retriever", inputs={"retrieved_chunks": chunks}) as child:
            child.set_outputs({"retrieved_count": 1})
        root.set_outputs({"answer": "Respuesta final"})

    assert [item["name"] for item in client.created] == ["rag.chat", "rag.retrieve"]
    assert client.created[1]["parent_run_id"] == client.created[0]["id"]
    assert client.created[1]["trace_id"] == client.created[0]["trace_id"]
    assert client.created[1]["inputs"]["retrieved_chunks"][0]["content"].startswith("Contenido privado")
    assert len(client.updated) == 2


def test_app_insights_span_attributes_do_not_include_user_text_or_chunk_content():
    chunk = sample_chunk()
    attrs = retrieval_span_attributes(retrieved=[chunk])
    safe_attrs = safe_span_attributes(
        {
            **attrs,
            "query.length": len("Como uso SafeGate?"),
            "message.length": len("Como uso SafeGate?"),
        }
    )
    serialized = str(safe_attrs)

    assert "Como uso SafeGate" not in serialized
    assert "Contenido privado" not in serialized
    assert safe_attrs["retrieval.chunk_ids"] == [42]
    assert safe_attrs["retrieval.source_keys"] == ["document:7"]


def test_safe_span_attributes_redacts_obvious_content_fields():
    attrs = safe_span_attributes(
        {
            "question": "Como uso SafeGate?",
            "prompt.messages": "Instrucciones internas",
            "answer": "Respuesta completa",
            "message.length": 17,
            "retrieval.text.limit": 5,
        }
    )

    assert attrs["question"] == "<redacted>"
    assert attrs["prompt.messages"] == "<redacted>"
    assert attrs["answer"] == "<redacted>"
    assert attrs["message.length"] == 17
    assert attrs["retrieval.text.limit"] == 5


def test_langsmith_chunk_serialization_includes_debug_content_and_scores():
    chunk_payload = serialize_chunks_for_langsmith([sample_chunk()])[0]

    assert chunk_payload["chunk_id"] == 42
    assert chunk_payload["source_key"] == "document:7"
    assert chunk_payload["title"] == "Procedimiento SafeGate"
    assert chunk_payload["final_score"] == 0.74
    assert "Contenido privado del chunk" in chunk_payload["content"]
