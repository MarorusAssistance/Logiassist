from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from internal_assistant.config import get_settings
from internal_assistant.repositories.retrieval import ChunkRepository
from internal_assistant.observability.tracing import retrieval_span_attributes, set_span_attributes, start_span
from internal_assistant.rag.filters import RetrievalFilters, normalize_retrieval_filters
from internal_assistant.rag.reranking import RerankerProvider, build_default_reranker


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: int
    source_type: str
    source_id: int
    content: str
    metadata: dict
    vector_score: float = 0.0
    text_score: float = 0.0
    hybrid_score: float = 0.0
    rerank_score: float = 0.0
    final_score: float = 0.0


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    top_k: int = 5
    vector_weight: float = 0.70
    text_weight: float = 0.30
    vector_candidates: int = 15
    text_candidates: int = 15

    def normalized(self) -> "RetrievalConfig":
        top_k = max(1, int(self.top_k))
        vector_candidates = max(top_k, int(self.vector_candidates))
        text_candidates = max(top_k, int(self.text_candidates))
        vector_weight = max(0.0, float(self.vector_weight))
        text_weight = max(0.0, float(self.text_weight))
        total_weight = vector_weight + text_weight
        if total_weight <= 0:
            vector_weight = 0.70
            text_weight = 0.30
            total_weight = 1.0
        return RetrievalConfig(
            top_k=top_k,
            vector_weight=vector_weight / total_weight,
            text_weight=text_weight / total_weight,
            vector_candidates=vector_candidates,
            text_candidates=text_candidates,
        )


DEFAULT_RETRIEVAL_CONFIG = RetrievalConfig()


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    maximum = max(scores)
    minimum = min(scores)
    if maximum == minimum:
        return [1.0 if maximum > 0 else 0.0 for _ in scores]
    return [(score - minimum) / (maximum - minimum) for score in scores]


class HybridRetriever:
    def __init__(self, session: Session, reranker: RerankerProvider | None = None):
        self.chunk_repository = ChunkRepository(session)
        self.reranker_candidates = max(1, int(get_settings().reranker_candidates))
        self.reranker = reranker if reranker is not None else build_default_reranker()

    def search(
        self,
        query: str,
        query_embedding: list[float],
        limit: int | None = None,
        config: RetrievalConfig | None = None,
        filters: RetrievalFilters | dict | None = None,
    ) -> list[RetrievedChunk]:
        effective = (config or DEFAULT_RETRIEVAL_CONFIG).normalized()
        if limit is not None:
            effective = replace(effective, top_k=max(1, int(limit))).normalized()
        effective_filters = normalize_retrieval_filters(filters)

        with start_span(
            "retrieval.hybrid_search",
            {
                "query.length": len(query),
                "retrieval.top_k": effective.top_k,
                "retrieval.vector_weight": effective.vector_weight,
                "retrieval.text_weight": effective.text_weight,
                "retrieval.vector_candidates": effective.vector_candidates,
                "retrieval.text_candidates": effective.text_candidates,
                "retrieval.filters.active": effective_filters.active(),
                "retrieval.filters": effective_filters.to_dict(),
                "retrieval.reranker.enabled": bool(self.reranker),
            },
        ) as span:
            vector_rows = self.chunk_repository.vector_search(
                query_embedding,
                limit=effective.vector_candidates,
                filters=effective_filters,
            )
            text_rows = self.chunk_repository.text_search(query, limit=effective.text_candidates, filters=effective_filters)
            filter_fallback = False

            with start_span(
                "retrieval.merge_rank",
                {
                    "retrieval.vector_candidates_returned": len(vector_rows),
                    "retrieval.text_candidates_returned": len(text_rows),
                },
            ) as merge_span:
                ordered = self._merge_rows(vector_rows, text_rows, effective)
                if effective_filters.active() and len(ordered) < effective.top_k:
                    filter_fallback = True
                    vector_rows = self.chunk_repository.vector_search(query_embedding, limit=effective.vector_candidates)
                    text_rows = self.chunk_repository.text_search(query, limit=effective.text_candidates)
                    ordered = self._merge_rows(vector_rows, text_rows, effective)

                reranked = self._rerank(query, ordered, effective.top_k)
                results = (reranked or ordered)[: effective.top_k]
                set_span_attributes(
                    merge_span,
                    {
                        **retrieval_span_attributes(retrieved=results, prefix="retrieval.merged"),
                        "retrieval.filter_fallback": filter_fallback,
                        "retrieval.pre_rerank_count": len(ordered),
                        "retrieval.post_rerank_count": len(results),
                    },
                )
            set_span_attributes(
                span,
                {
                    **retrieval_span_attributes(retrieved=results),
                    "retrieval.filter_fallback": filter_fallback,
                },
            )
            return results

    def _merge_rows(self, vector_rows: list[dict], text_rows: list[dict], config: RetrievalConfig) -> list[RetrievedChunk]:
        merged: dict[int, RetrievedChunk] = {}
        for row in vector_rows:
            merged[row["id"]] = RetrievedChunk(
                chunk_id=row["id"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                content=row["content"],
                metadata=row["metadata"] or {},
                vector_score=float(row["score"] or 0.0),
            )

        for row in text_rows:
            existing = merged.get(row["id"])
            if existing:
                existing.text_score = float(row["score"] or 0.0)
            else:
                merged[row["id"]] = RetrievedChunk(
                    chunk_id=row["id"],
                    source_type=row["source_type"],
                    source_id=row["source_id"],
                    content=row["content"],
                    metadata=row["metadata"] or {},
                    text_score=float(row["score"] or 0.0),
                )

        ordered = list(merged.values())
        vector_norm = _normalize([chunk.vector_score for chunk in ordered])
        text_norm = _normalize([chunk.text_score for chunk in ordered])

        for idx, chunk in enumerate(ordered):
            chunk.hybrid_score = config.vector_weight * vector_norm[idx] + config.text_weight * text_norm[idx]
            chunk.final_score = chunk.hybrid_score

        ordered.sort(key=lambda item: item.final_score, reverse=True)
        return ordered

    def _rerank(self, query: str, ordered: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk] | None:
        if not self.reranker or not ordered:
            return None

        candidate_limit = max(top_k, int(getattr(self, "reranker_candidates", len(ordered))))
        candidates = ordered[:candidate_limit]
        documents = [
            {
                "id": chunk.chunk_id,
                "text": chunk.content,
                "metadata": {
                    **(chunk.metadata or {}),
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "hybrid_score": chunk.hybrid_score,
                },
            }
            for chunk in candidates
        ]
        started_at = time.perf_counter()
        with start_span(
            "retrieval.rerank",
            {
                "retrieval.reranker.model": getattr(self.reranker, "model", ""),
                "retrieval.reranker.candidate_count": len(documents),
                "retrieval.reranker.top_k": top_k,
            },
        ) as span:
            try:
                results = self.reranker.rerank(query=query, documents=documents, top_n=top_k)
            except Exception as exc:
                logger.warning("Reranker unavailable; using hybrid ranking: %s", exc)
                set_span_attributes(
                    span,
                    {
                        "retrieval.reranker.error": type(exc).__name__,
                        "retrieval.reranker.fallback": True,
                    },
                )
                return None

            score_by_id = {item.chunk_id: item.score for item in results}
            rerank_norm = _normalize([score_by_id.get(chunk.chunk_id, 0.0) for chunk in candidates])
            hybrid_norm = _normalize([chunk.hybrid_score for chunk in candidates])
            for idx, chunk in enumerate(candidates):
                chunk.rerank_score = score_by_id.get(chunk.chunk_id, 0.0)
                chunk.final_score = 0.80 * rerank_norm[idx] + 0.20 * hybrid_norm[idx]

            reranked_candidates = sorted(candidates, key=lambda item: item.final_score, reverse=True)
            untouched = ordered[candidate_limit:]
            reranked = reranked_candidates + untouched
            set_span_attributes(
                span,
                {
                    "retrieval.reranker.latency_ms": int((time.perf_counter() - started_at) * 1000),
                    "retrieval.reranker.result_count": len(results),
                    "retrieval.reranker.chunk_ids": [item.chunk_id for item in reranked[:top_k]],
                },
            )
            return reranked
