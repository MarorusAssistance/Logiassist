from __future__ import annotations

import pytest

from internal_assistant.rag.filters import RetrievalFilters
from internal_assistant.rag.reranking import CohereAzureRerankerProvider
from internal_assistant.rag.retrieval import HybridRetriever
from internal_assistant.repositories.retrieval import _filters_sql


class StubChunkRepository:
    def __init__(self):
        self.vector_filters = []
        self.text_filters = []

    def vector_search(self, embedding, limit=15, filters=None):
        self.vector_filters.append(filters)
        return [
            {"id": 1, "source_type": "document", "source_id": 10, "content": "alpha", "metadata": {"title": "Doc A"}, "score": 0.9},
            {"id": 2, "source_type": "incident", "source_id": 20, "content": "beta", "metadata": {"title": "Inc B"}, "score": 0.5},
        ]

    def text_search(self, query, limit=15, filters=None):
        self.text_filters.append(filters)
        return [
            {"id": 2, "source_type": "incident", "source_id": 20, "content": "beta", "metadata": {"title": "Inc B"}, "score": 0.8},
            {"id": 3, "source_type": "document", "source_id": 30, "content": "gamma", "metadata": {"title": "Doc C"}, "score": 0.4},
        ]


def test_hybrid_retrieval_fuses_with_rrf_and_orders_top_results():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = None

    results = retriever.search("consulta", [0.1] * 512, limit=3)

    # Chunk 2 aparece en ambas listas (vector #2, BM25 #1): RRF lo pone primero.
    assert [item.chunk_id for item in results] == [2, 1, 3]
    assert results[0].final_score >= results[1].final_score >= results[2].final_score
    assert 0 < results[0].final_score <= 1


def test_rrf_with_equal_weights_ties_symmetric_ranks():
    from internal_assistant.rag.retrieval import RetrievalConfig

    retriever = HybridRetriever.__new__(HybridRetriever)
    vector_rows = [{"id": 1, "source_type": "document", "source_id": 1, "content": "a", "metadata": {}, "score": 0.9}]
    text_rows = [{"id": 2, "source_type": "document", "source_id": 2, "content": "b", "metadata": {}, "score": 7.0}]
    merged = retriever._merge_rows(vector_rows, text_rows, RetrievalConfig().normalized())
    assert merged[0].final_score == pytest.approx(merged[1].final_score)


def test_token_budget_drops_worst_chunks_until_it_fits():
    from internal_assistant.rag.retrieval import RetrievedChunk

    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.context_max_tokens = 100
    chunks = [RetrievedChunk(chunk_id=i, source_type="document", source_id=i, content="x" * 150, metadata={}) for i in range(4)]
    kept = retriever._fit_token_budget(chunks)
    assert [chunk.chunk_id for chunk in kept] == [0, 1]


def test_reranker_score_alone_decides_final_order():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = FakeReranker()
    retriever.reranker_candidates = 100

    results = retriever.search("consulta", [0.1] * 512, limit=3)

    assert [item.chunk_id for item in results] == [3, 1, 2]
    assert results[0].final_score == pytest.approx(0.99)
    assert results[-1].final_score == 0.0


def test_hybrid_retrieval_passes_filters_to_repository():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = None
    filters = RetrievalFilters(source_types=["document"], affected_systems=["SafeGate"])

    retriever.search("consulta", [0.1] * 512, limit=2, filters=filters)

    assert retriever.chunk_repository.vector_filters[0] == filters
    assert retriever.chunk_repository.text_filters[0] == filters


def test_chunk_repository_filter_sql_includes_metadata_columns():
    sql, params = _filters_sql(
        RetrievalFilters(
            source_types=["document"],
            affected_systems=["SafeGate"],
            document_types=["procedimiento"],
            is_resolved=True,
            tags_any=["acceso"],
        )
    )

    assert "source_type = ANY" in sql
    assert "affected_system = ANY" in sql
    assert "document_type = ANY" in sql
    assert "is_resolved = :is_resolved" in sql
    assert "tags &&" in sql
    assert params["source_types"] == ["document"]
    assert params["affected_systems"] == ["SafeGate"]
    assert params["document_types"] == ["procedimiento"]
    assert params["is_resolved"] is True
    assert params["tags_any"] == ["acceso"]


class EmptyThenFallbackRepository(StubChunkRepository):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def vector_search(self, embedding, limit=15, filters=None):
        self.calls += 1
        if filters and filters.active():
            return []
        return super().vector_search(embedding, limit=limit, filters=filters)

    def text_search(self, query, limit=15, filters=None):
        if filters and filters.active():
            return []
        return super().text_search(query, limit=limit, filters=filters)


def test_hybrid_retrieval_falls_back_when_filters_return_too_few_results():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = EmptyThenFallbackRepository()
    retriever.reranker = None

    results = retriever.search("consulta", [0.1] * 512, limit=2, filters=RetrievalFilters(source_types=["document"]))

    assert len(results) == 2
    assert retriever.chunk_repository.calls >= 2


class FakeReranker:
    model = "fake-reranker"

    def rerank(self, *, query, documents, top_n):
        return [
            type("Result", (), {"chunk_id": 3, "score": 0.99})(),
            type("Result", (), {"chunk_id": 1, "score": 0.10})(),
        ]


def test_hybrid_retrieval_reranks_candidates():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = FakeReranker()
    retriever.reranker_candidates = 30

    results = retriever.search("consulta", [0.1] * 512, limit=3)

    assert results[0].chunk_id == 3
    assert results[0].rerank_score == pytest.approx(0.99)


class FailingReranker:
    model = "failing-reranker"

    def rerank(self, *, query, documents, top_n):
        raise RuntimeError("down")


def test_hybrid_retrieval_keeps_hybrid_order_when_reranker_fails():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = FailingReranker()
    retriever.reranker_candidates = 30

    results = retriever.search("consulta", [0.1] * 512, limit=3)

    assert [item.chunk_id for item in results] == [2, 1, 3]


def test_cohere_azure_reranker_maps_result_indexes_to_chunk_ids(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.87},
                    {"index": 0, "relevance_score": 0.21},
                ]
            }

    def fake_post(url, *, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("internal_assistant.rag.reranking.httpx.post", fake_post)
    provider = CohereAzureRerankerProvider(
        base_url="https://cohere-rerank.example.inference.ai.azure.com",
        api_key="test-key",
        model="rerank-v4.0-fast",
        timeout_seconds=9,
    )

    results = provider.rerank(
        query="consulta",
        documents=[
            {"id": 10, "text": "primer texto", "metadata": {"source_title": "Doc A", "affected_system": "AlmaTrack WMS"}},
            {"id": 20, "text": "segundo texto", "metadata": {"source_title": "Doc B", "source_type": "document"}},
        ],
        top_n=2,
    )

    assert [item.chunk_id for item in results] == [20, 10]
    assert results[0].score == pytest.approx(0.87)
    assert calls[0]["url"].endswith("/v1/rerank")
    assert calls[0]["json"]["model"] == "rerank-v4.0-fast"
    assert calls[0]["json"]["documents"][0].startswith("title: Doc A")
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"


def test_cohere_azure_reranker_accepts_full_rerank_path():
    provider = CohereAzureRerankerProvider(
        base_url="https://example.services.ai.azure.com/models/providers/cohere/v2/rerank",
        api_key="test-key",
        model="rerank-v4.0-fast",
        timeout_seconds=9,
    )

    assert provider.endpoint_url == "https://example.services.ai.azure.com/models/providers/cohere/v2/rerank"


def test_reranker_post_retries_on_429(monkeypatch):
    import httpx

    from internal_assistant.rag import reranking

    request = httpx.Request("POST", "https://x/rerank")
    responses = [
        httpx.Response(429, request=request, headers={"retry-after": "1"}),
        httpx.Response(200, request=request, json={"results": []}),
    ]
    sleeps: list[float] = []
    monkeypatch.setattr(reranking.httpx, "post", lambda url, **kwargs: responses.pop(0))
    monkeypatch.setattr(reranking.time, "sleep", sleeps.append)

    response = reranking._post_with_retry("https://x/rerank", json={})

    assert response.status_code == 200
    assert sleeps == [1.0]


def test_bm25_rank_prefers_rare_terms_and_short_documents():
    from internal_assistant.repositories.retrieval import bm25_rank

    rows = [
        {"id": 1, "doc_len": 100, "term_freqs": {"safegat": 1}},
        {"id": 2, "doc_len": 20, "term_freqs": {"safegat": 1}},
        {"id": 3, "doc_len": 20, "term_freqs": {"acces": 1}},
    ]
    ranked = bm25_rank(rows, document_frequency={"safegat": 2, "acces": 50}, total_documents=100, average_length=40)
    assert [row["id"] for row in ranked] == [2, 1, 3]


def test_keyword_extraction_drops_filler_and_keeps_codes():
    from internal_assistant.rag.keywords import build_keyword_query, extract_keywords

    assert extract_keywords("Donde esta el boton para subir una incidencia?") == ["boton", "subir", "incidencia"]
    assert extract_keywords("Que hago si ScanBridge IDP marca un CMR como ilegible") == ["ScanBridge", "IDP", "marca", "CMR", "ilegible"]
    assert build_keyword_query(["SafeGate", "acceso"], "acceso temporal en SafeGate") == "SafeGate acceso temporal"


def test_voyage_reranker_maps_results_and_records_tokens(monkeypatch):
    import httpx

    from internal_assistant.observability.usage import metering
    from internal_assistant.rag import reranking
    from internal_assistant.rag.reranking import VoyageRerankerProvider

    calls = []
    request = httpx.Request("POST", "https://eu.ai.mongodb.com/v1/rerank")

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        body = {"data": [{"index": 1, "relevance_score": 0.91}, {"index": 0, "relevance_score": 0.12}], "usage": {"total_tokens": 321}}
        return httpx.Response(200, request=request, json=body)

    monkeypatch.setattr(reranking.httpx, "post", fake_post)
    provider = VoyageRerankerProvider(base_url="https://eu.ai.mongodb.com", api_key="k", model="rerank-3-lite", timeout_seconds=5)

    with metering() as meter:
        results = provider.rerank(
            query="consulta",
            documents=[{"id": 10, "text": "a", "metadata": {"title": "Doc A"}}, {"id": 20, "text": "b", "metadata": {}}],
            top_n=2,
        )

    assert [item.chunk_id for item in results] == [20, 10]
    assert calls[0]["url"] == "https://eu.ai.mongodb.com/v1/rerank"
    assert calls[0]["json"]["model"] == "rerank-3-lite" and calls[0]["json"]["top_k"] == 2
    assert calls[0]["json"]["documents"][0].startswith("title: Doc A")
    assert meter.events[0].input_tokens == 321 and meter.events[0].provider == "voyage"


def test_voyage_rerank_tokens_are_priced():
    from evaluation.cost import load_pricing, price_usage

    usd, _ = price_usage(
        {"component": "reranker", "provider": "voyage", "model": "rerank-3-lite", "input_tokens": 1_000_000, "search_units": 0},
        load_pricing(),
    )
    assert usd == pytest.approx(0.02)


def test_voyage_reranker_uses_generic_reranker_settings(monkeypatch):
    from types import SimpleNamespace

    from internal_assistant.rag import reranking

    settings = SimpleNamespace(
        reranker_enabled=True,
        reranker_provider="voyage",
        reranker_base_url="https://eu.ai.mongodb.com",
        reranker_api_key="k",
        reranker_model="rerank-3",
        reranker_timeout_seconds=5,
    )
    monkeypatch.setattr(reranking, "get_settings", lambda: settings)
    provider = reranking.build_default_reranker()
    assert isinstance(provider, reranking.VoyageRerankerProvider)
    assert provider.model == "rerank-3" and provider.endpoint_url == "https://eu.ai.mongodb.com/v1/rerank"


def test_only_hard_filters_reach_the_repository():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = None
    planner_filters = RetrievalFilters(
        source_types=["document"], affected_systems=["SafeGate"], document_types=["procedimiento"], departments=["Seguridad"]
    )

    retriever.search("consulta", [0.1] * 512, limit=2, filters=planner_filters)

    applied = retriever.chunk_repository.vector_filters[0]
    assert applied.source_types == ["document"] and applied.affected_systems == ["SafeGate"]
    assert applied.document_types == [] and applied.departments == []


class LowScoreFilteredRepository(StubChunkRepository):
    def vector_search(self, embedding, limit=15, filters=None):
        self.vector_filters.append(filters)
        if filters and filters.active():
            return [{"id": 1, "source_type": "document", "source_id": 10, "content": "x", "metadata": {}, "score": 0.5}]
        return [{"id": 3, "source_type": "document", "source_id": 30, "content": "y", "metadata": {}, "score": 0.9}]

    def text_search(self, query, limit=15, filters=None):
        return []


class ScoreByIdReranker:
    model = "fake"

    def rerank(self, *, query, documents, top_n):
        scores = {1: 0.2, 3: 0.9}
        return [type("R", (), {"chunk_id": d["id"], "score": scores.get(d["id"], 0.0)})() for d in documents]


def test_weak_filtered_results_retry_without_filters():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = LowScoreFilteredRepository()
    retriever.reranker = ScoreByIdReranker()
    retriever.reranker_candidates = 100
    retriever.confidence_threshold = 0.5

    results = retriever.search("consulta", [0.1] * 512, limit=1, filters=RetrievalFilters(source_types=["document"]))

    assert [item.chunk_id for item in results] == [3]
