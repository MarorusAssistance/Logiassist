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


def test_hybrid_retrieval_combines_scores_and_orders_top_results():
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever.chunk_repository = StubChunkRepository()
    retriever.reranker = None

    results = retriever.search("consulta", [0.1] * 512, limit=3)

    assert [item.chunk_id for item in results] == [1, 2, 3]
    assert results[0].final_score >= results[1].final_score >= results[2].final_score


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

    assert [item.chunk_id for item in results] == [1, 2, 3]


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
