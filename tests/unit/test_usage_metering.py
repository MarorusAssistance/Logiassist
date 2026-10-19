from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

from evaluation.cost import build_cost_section, load_pricing, price_usage, render_cost_markdown, usage_row_fields
from internal_assistant.llm.openai_compatible_provider import OpenAICompatibleProvider
from internal_assistant.llm.openai_provider import OpenAIProvider
from internal_assistant.observability.usage import (
    UsageMeter,
    current_meter,
    metering,
    record_chat_usage,
    record_usage,
)
from internal_assistant.rag import reranking


def _row(component, *, provider="openai", model="", input_tokens=0, cached=0, output=0, units=0.0):
    return {
        "component": component,
        "purpose": "x",
        "provider": provider,
        "model": model,
        "requests": 1,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "search_units": units,
    }


# --- UsageMeter -----------------------------------------------------------------


def test_record_usage_is_noop_without_active_meter():
    assert current_meter() is None
    record_usage(component="chat", purpose="answer", provider="openai", model="gpt-4o-mini", input_tokens=10)
    assert current_meter() is None


def test_metering_records_and_aggregates_events():
    with metering() as meter:
        record_usage(component="chat", purpose="answer", provider="openai", model="gpt-4o-mini", input_tokens=100, output_tokens=20)
        record_usage(component="chat", purpose="answer", provider="openai", model="gpt-4o-mini", input_tokens=50, cached_input_tokens=10)
        record_usage(component="chat", purpose="planner", provider="openai", model="gpt-4o-mini", input_tokens=7)
    assert current_meter() is None
    rows = {row["purpose"]: row for row in meter.aggregate()}
    assert rows["answer"]["requests"] == 2
    assert rows["answer"]["input_tokens"] == 150
    assert rows["answer"]["cached_input_tokens"] == 10
    assert rows["answer"]["output_tokens"] == 20
    assert rows["planner"]["input_tokens"] == 7


def test_nested_metering_propagates_to_parent_unless_disabled():
    with metering() as outer:
        with metering() as inner:
            record_usage(component="embeddings", purpose="embedding", provider="openai", model="m", input_tokens=5)
        with metering(propagate=False) as isolated:
            record_usage(component="embeddings", purpose="embedding", provider="openai", model="m", input_tokens=3)
    assert len(inner) == 1
    assert len(isolated) == 1
    assert sum(row["input_tokens"] for row in outer.aggregate()) == 5


def test_meter_is_thread_safe_and_mergeable_from_workers():
    def worker(_):
        with metering(propagate=False) as meter:
            for _ in range(100):
                record_usage(component="reranker", purpose="rerank", provider="cohere_azure", model="rerank-v3.5", search_units=1)
        return meter

    total = UsageMeter()
    with ThreadPoolExecutor(max_workers=4) as executor:
        for meter in executor.map(worker, range(8)):
            total.merge(meter)
    assert total.aggregate()[0]["search_units"] == 800


def test_record_chat_usage_tolerates_missing_usage():
    with metering() as meter:
        record_chat_usage(None, provider="openai_compatible", model="local", purpose="answer")
    assert meter.aggregate()[0]["input_tokens"] == 0


# --- Pricing --------------------------------------------------------------------


def test_chat_pricing_applies_cached_input_discount():
    pricing = load_pricing()
    usd, _ = price_usage(_row("chat", model="gpt-4o-mini", input_tokens=1_000_000, cached=400_000, output=1_000_000), pricing)
    # 600k * 0.15 + 400k * 0.075 + 1M * 0.60
    assert usd == pytest.approx(0.09 + 0.03 + 0.60)


def test_pricing_matches_dated_snapshots_by_prefix():
    pricing = load_pricing()
    usd, _ = price_usage(_row("chat", model="gpt-4o-2024-08-06", input_tokens=1_000_000), pricing)
    assert usd == pytest.approx(2.50)
    usd_mini, _ = price_usage(_row("chat", model="gpt-4o-mini-2024-07-18", input_tokens=1_000_000), pricing)
    assert usd_mini == pytest.approx(0.15)


def test_embedding_pricing():
    usd, _ = price_usage(_row("embeddings", model="text-embedding-3-small", input_tokens=2_000_000), load_pricing())
    assert usd == pytest.approx(0.04)


def test_unknown_model_returns_none_and_note_without_crashing():
    usd, note = price_usage(_row("chat", model="modelo-inventado", input_tokens=10), load_pricing())
    assert usd is None
    assert "modelo-inventado" in note


def test_local_providers_cost_zero():
    pricing = load_pricing()
    assert price_usage(_row("chat", provider="openai_compatible", model="qwen", input_tokens=10**6), pricing)[0] == 0.0
    assert price_usage(_row("reranker", provider="http", model="BAAI/bge-reranker-v2-m3", units=0), pricing)[0] == 0.0


def test_reranker_priced_by_search_units_with_provider_default_model():
    pricing = load_pricing()
    usd, _ = price_usage(_row("reranker", provider="cohere_azure", model="Cohere-rerank-v3.5", units=3), pricing)
    assert usd == pytest.approx(0.006)
    usd_default, note = price_usage(_row("reranker", provider="cohere_azure", model="model", units=1000), pricing)
    assert usd_default == pytest.approx(2.0)
    assert "rerank-v3.5" in note


def test_pricing_path_can_be_overridden(tmp_path, monkeypatch):
    custom = tmp_path / "pricing.json"
    custom.write_text(
        json.dumps({"_meta": {"verified_at": "test"}, "chat": {"gpt-4o-mini": {"input_per_1m": 1.0, "output_per_1m": 1.0}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVAL_PRICING_PATH", str(custom))
    pricing = load_pricing()
    assert pricing["_meta"]["verified_at"] == "test"
    usd, _ = price_usage(_row("chat", model="gpt-4o-mini", input_tokens=1_000_000, cached=1_000_000), pricing)
    # Sin precio de cached input se usa el de input.
    assert usd == pytest.approx(1.0)


def test_build_cost_section_breakdown_totals_and_markdown():
    with metering() as meter:
        record_usage(component="chat", purpose="planner", provider="openai", model="gpt-4o-mini", input_tokens=1000, output_tokens=100)
        record_usage(component="chat", purpose="answer", provider="openai", model="gpt-4o-mini", input_tokens=2000, cached_input_tokens=1000, output_tokens=200)
        record_usage(component="chat", purpose="judge", provider="openai", model="gpt-4o-mini", input_tokens=500, output_tokens=50)
        record_usage(component="embeddings", purpose="embedding", provider="openai", model="text-embedding-3-small", input_tokens=40)
        record_usage(component="reranker", purpose="rerank", provider="cohere_azure", model="rerank-v3.5", search_units=1)
        record_usage(component="chat", purpose="answer", provider="openai", model="desconocido", input_tokens=1)

    cost = build_cost_section(meter, turns=2, scenarios=1)
    assert cost["complete"] is False
    assert {row["purpose"] for row in cost["breakdown"]} == {"planner", "answer", "judge", "embedding", "rerank"}
    expected_chat = (1000 * 0.15 + 100 * 0.6 + 1000 * 0.15 + 1000 * 0.075 + 200 * 0.6 + 500 * 0.15 + 50 * 0.6) / 1e6
    assert cost["by_component_usd"]["chat"] == pytest.approx(expected_chat)
    assert cost["by_component_usd"]["reranker"] == pytest.approx(0.002)
    assert cost["total_usd"] == pytest.approx(expected_chat + 40 * 0.02 / 1e6 + 0.002)
    assert cost["avg_usd_per_turn"] == pytest.approx(cost["total_usd"] / 2, rel=1e-6)
    assert cost["pricing_source"]["updated_at"]

    markdown = render_cost_markdown(cost)
    assert markdown.startswith("## Cost")
    assert "| chat | judge | openai | gpt-4o-mini |" in markdown
    assert "n/d" in markdown

    fields = usage_row_fields(meter)
    assert fields["cost_usd"] is None
    assert fields["chat_cached_input_tokens"] == 1000
    assert fields["rerank_search_units"] == 1


def test_empty_meter_costs_zero():
    cost = build_cost_section(UsageMeter(), turns=0)
    assert cost["total_usd"] == 0.0
    assert cost["complete"] is True
    assert usage_row_fields(None)["cost_usd"] == 0.0


# --- Providers --------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _fake_provider(cls, *, chat_response=None, embedding_response=None):
    provider = cls.__new__(cls)
    provider.settings = SimpleNamespace(
        chat_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
        embedding_dimensions=2,
        llm_timeout_seconds=5,
    )
    completions = _FakeCompletions(chat_response)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        embeddings=_FakeCompletions(embedding_response),
    )
    return provider, completions


_DECISION_JSON = json.dumps(
    {"answer": "ok", "needs_clarification": False, "should_offer_incident": False, "used_chunk_ids": []}
)


def test_openai_provider_records_chat_usage_with_cached_tokens():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=_DECISION_JSON))],
        usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=80, prompt_tokens_details=SimpleNamespace(cached_tokens=1024)),
    )
    provider, _ = _fake_provider(OpenAIProvider, chat_response=response)
    with metering() as meter:
        provider.generate_chat_response(question="hola", context_chunks=[], conversation_state={})
    [row] = meter.aggregate()
    assert row == {
        "component": "chat",
        "purpose": "answer",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "requests": 1,
        "input_tokens": 1200,
        "cached_input_tokens": 1024,
        "output_tokens": 80,
        "search_units": 0.0,
    }


def test_openai_provider_records_planner_and_embedding_usage():
    plan_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=300, completion_tokens=40, prompt_tokens_details=None),
    )
    embedding_response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])],
        usage=SimpleNamespace(prompt_tokens=9, total_tokens=9),
    )
    provider, _ = _fake_provider(OpenAIProvider, chat_response=plan_response, embedding_response=embedding_response)
    with metering() as meter:
        provider.plan_chat(message="hola", recent_messages=[], conversation_state={})
        provider.embed_texts(["hola"])
    rows = {row["component"]: row for row in meter.aggregate()}
    assert rows["chat"]["purpose"] == "planner"
    assert rows["chat"]["input_tokens"] == 300
    assert rows["chat"]["cached_input_tokens"] == 0
    assert rows["embeddings"]["input_tokens"] == 9
    assert rows["embeddings"]["model"] == "text-embedding-3-small"


def test_openai_provider_streaming_requests_and_records_usage():
    def chunk(text):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None)

    final = SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=12, prompt_tokens_details=None))
    stream = [chunk(_DECISION_JSON[:10]), chunk(_DECISION_JSON[10:]), final]
    provider, completions = _fake_provider(OpenAIProvider, chat_response=stream)
    with metering() as meter:
        events = list(provider.stream_chat_response(question="hola", context_chunks=[], conversation_state={}))
    assert completions.kwargs["stream_options"] == {"include_usage": True}
    assert events[-1].kind == "final"
    [row] = meter.aggregate()
    assert (row["purpose"], row["input_tokens"], row["output_tokens"]) == ("answer", 50, 12)


def test_openai_compatible_provider_records_tokens_even_without_usage():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=_DECISION_JSON))], usage=None)
    provider, _ = _fake_provider(OpenAICompatibleProvider, chat_response=response)
    with metering() as meter:
        provider.generate_chat_response(question="hola", context_chunks=[], conversation_state={})
    [row] = meter.aggregate()
    assert row["provider"] == "openai_compatible"
    assert row["input_tokens"] == 0
    cost = build_cost_section(meter, turns=1)
    assert cost["total_usd"] == 0.0
    assert cost["complete"] is True


# --- Rerankers --------------------------------------------------------------------


class _FakeHttpResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_cohere_reranker_records_billed_search_units(monkeypatch):
    payload = {"results": [{"index": 0, "relevance_score": 0.9}], "meta": {"billed_units": {"search_units": 2}}}
    monkeypatch.setattr(reranking.httpx, "post", lambda *args, **kwargs: _FakeHttpResponse(payload))
    provider = reranking.CohereAzureRerankerProvider(base_url="https://x", api_key="k", model="Cohere-rerank-v3.5", timeout_seconds=1)
    with metering() as meter:
        results = provider.rerank(query="q", documents=[{"id": 7, "text": "t", "metadata": {}}], top_n=1)
    assert results[0].chunk_id == 7
    [row] = meter.aggregate()
    assert (row["component"], row["provider"], row["search_units"]) == ("reranker", "cohere_azure", 2.0)


def test_cohere_reranker_falls_back_to_one_search_unit(monkeypatch):
    payload = {"results": []}
    monkeypatch.setattr(reranking.httpx, "post", lambda *args, **kwargs: _FakeHttpResponse(payload))
    provider = reranking.CohereAzureRerankerProvider(base_url="https://x", api_key="k", model="model", timeout_seconds=1)
    with metering() as meter:
        provider.rerank(query="q", documents=[], top_n=1)
    assert meter.aggregate()[0]["search_units"] == 1.0


def test_http_reranker_records_request_with_zero_cost(monkeypatch):
    monkeypatch.setattr(reranking.httpx, "post", lambda *args, **kwargs: _FakeHttpResponse({"results": [{"id": 3, "score": 0.5}]}))
    provider = reranking.HttpRerankerProvider(base_url="http://local", model="BAAI/bge-reranker-v2-m3", timeout_seconds=1)
    with metering() as meter:
        provider.rerank(query="q", documents=[], top_n=1)
    [row] = meter.aggregate()
    assert row["provider"] == "http"
    assert row["requests"] == 1
    assert build_cost_section(meter, turns=1)["total_usd"] == 0.0
