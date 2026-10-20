from __future__ import annotations

import json
from types import SimpleNamespace

from internal_assistant.observability.trace_store import record_llm_call, record_trace_event
from internal_assistant.observability.tracing import RagTrace
from internal_assistant.observability.usage import record_usage


def _settings(tmp_path, **overrides):
    values = dict(
        langsmith_tracing=False,
        langsmith_api_key="",
        langsmith_project="test",
        langsmith_endpoint="",
        trace_store="local",
        trace_store_dir=str(tmp_path),
        trace_store_container="",
        trace_store_account_url="",
        trace_store_connection_string="",
        trace_client="cliente",
        trace_project="proyecto",
        app_name="app",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _written(tmp_path) -> tuple[str, dict]:
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    return str(files[0].relative_to(tmp_path)), json.loads(files[0].read_text(encoding="utf-8"))


def test_request_trace_is_stored_per_client_project_conversation_and_request(tmp_path, monkeypatch):
    monkeypatch.setattr("internal_assistant.observability.tracing.store_trace", lambda d, p, s: s.write(p, d))
    trace = RagTrace(settings=_settings(tmp_path))

    with trace.start_run("rag.chat", inputs={"question": "Como pido acceso en SafeGate?"}) as root:
        root.update_metadata(conversation_id=42)
        with root.child("rag.plan", run_type="llm", inputs={"message": "hola"}) as plan:
            record_llm_call(purpose="planner", provider="p", model="m", messages=[{"role": "user", "content": "x"}], raw_output="{}")
            record_usage(component="chat", purpose="planner", provider="p", model="m", input_tokens=10, output_tokens=2)
            plan.set_outputs({"planner_output": {"search_keywords": ["SafeGate", "acceso"]}})
        record_trace_event("evidence_gate", {"confidence": 0.71})
        root.set_outputs({"answer": "respuesta"})

    path, document = _written(tmp_path)
    assert path.startswith("cliente/proyecto/conversation-42/")
    assert path.endswith(f"_{root.request_id}.json")
    assert document["request_id"] == root.request_id
    assert document["inputs"]["question"] == "Como pido acceso en SafeGate?"
    assert document["outputs"] == {"answer": "respuesta"}
    assert [run["name"] for run in document["runs"]] == ["rag.plan"]
    assert document["runs"][0]["outputs"]["planner_output"]["search_keywords"] == ["SafeGate", "acceso"]
    assert [event["name"] for event in document["events"]] == ["llm.call", "evidence_gate"]
    assert document["usage"][0]["input_tokens"] == 10


def test_simulation_traces_are_grouped_under_eval(tmp_path, monkeypatch):
    monkeypatch.setattr("internal_assistant.observability.tracing.store_trace", lambda d, p, s: s.write(p, d))
    trace = RagTrace(settings=_settings(tmp_path))

    with trace.start_run("rag.chat", metadata={"mode": "simulation", "eval_scenario_id": "eval-037"}):
        pass

    path, _ = _written(tmp_path)
    assert path.startswith("cliente/proyecto/eval/eval-037/")


def test_trace_store_disabled_writes_nothing_and_events_are_noop(tmp_path):
    trace = RagTrace(settings=_settings(tmp_path, trace_store="none"))

    with trace.start_run("rag.chat") as root:
        record_trace_event("evidence_gate", {"confidence": 0.1})
        root.set_outputs({"answer": "ok"})

    assert list(tmp_path.rglob("*.json")) == []


def test_retriever_records_every_stage(tmp_path, monkeypatch):
    from internal_assistant.rag.retrieval import HybridRetriever

    monkeypatch.setattr("internal_assistant.observability.tracing.store_trace", lambda d, p, s: s.write(p, d))

    class Repo:
        def vector_search(self, embedding, limit=15, filters=None):
            return [{"id": 1, "source_type": "document", "source_id": 10, "content": "a" * 64, "metadata": {"title": "Doc A"}, "score": 0.9}]

        def text_search(self, query, limit=15, filters=None):
            return [{"id": 2, "source_type": "incident", "source_id": 20, "content": "b" * 64, "metadata": {"title": "Inc B"}, "score": 3.2}]

    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = Repo()
    retriever.reranker = None
    retriever.context_max_tokens = 16000

    with RagTrace(settings=_settings(tmp_path)).start_run("rag.chat"):
        retriever.search("consulta", [0.1] * 4, limit=2, keyword_query="consulta clave")

    _, document = _written(tmp_path)
    event = next(item for item in document["events"] if item["name"] == "retrieval.hybrid_search")
    stages = event["stages"]
    assert event["keyword_query"] == "consulta clave"
    assert [row["chunk_id"] for row in stages["vector"]] == [1]
    assert [row["chunk_id"] for row in stages["bm25"]] == [2]
    assert {row["chunk_id"] for row in stages["fused"]} == {1, 2}
    assert len(stages["returned"]) == 2
    assert stages["token_budget"]["dropped"] == []
