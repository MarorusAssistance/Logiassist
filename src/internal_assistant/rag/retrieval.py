from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from internal_assistant.config import get_settings
from internal_assistant.repositories.retrieval import ChunkRepository
from internal_assistant.observability.trace_store import current_recorder, record_trace_event
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
    """Busqueda hibrida al estilo Azure AI Search (BM25 + vector, fusion RRF, reranker semantico).

    vector_weight/text_weight son los pesos de cada lista en la fusion RRF (como el `weight`
    de las consultas vectoriales de Azure). Por defecto pesan lo mismo.
    """

    top_k: int = 16
    vector_weight: float = 0.50
    text_weight: float = 0.50
    vector_candidates: int = 50
    text_candidates: int = 50
    rrf_k: int = 60

    def normalized(self) -> "RetrievalConfig":
        top_k = max(1, int(self.top_k))
        vector_candidates = max(top_k, int(self.vector_candidates))
        text_candidates = max(top_k, int(self.text_candidates))
        vector_weight = max(0.0, float(self.vector_weight))
        text_weight = max(0.0, float(self.text_weight))
        total_weight = vector_weight + text_weight
        if total_weight <= 0:
            vector_weight = 0.50
            text_weight = 0.50
            total_weight = 1.0
        return RetrievalConfig(
            top_k=top_k,
            vector_weight=vector_weight / total_weight,
            text_weight=text_weight / total_weight,
            vector_candidates=vector_candidates,
            text_candidates=text_candidates,
            rrf_k=max(1, int(self.rrf_k)),
        )


DEFAULT_RETRIEVAL_CONFIG = RetrievalConfig()


def _source_keys(items: list) -> list[str]:
    keys: list[str] = []
    for item in items:
        if isinstance(item, dict):
            key = f"{item['source_type']}:{item['source_id']}"
        else:
            key = f"{item.source_type}:{item.source_id}"
        if key not in keys:
            keys.append(key)
    return keys


def _title(metadata: dict | None) -> str:
    metadata = metadata or {}
    return str(metadata.get("title") or metadata.get("source_title") or "")


def _row_stage(rows: list[dict]) -> list[dict]:
    return [
        {
            "rank": rank,
            "chunk_id": row["id"],
            "source": f"{row['source_type']}:{row['source_id']}",
            "title": _title(row.get("metadata")),
            "score": round(float(row.get("score") or 0.0), 6),
        }
        for rank, row in enumerate(rows, start=1)
    ]


def _retrieval_stages(
    *,
    vector_rows: list[dict],
    text_rows: list[dict],
    ordered: list["RetrievedChunk"],
    reranked: list["RetrievedChunk"] | None,
    reranker_candidates: int,
    top: list["RetrievedChunk"],
    results: list["RetrievedChunk"],
    budget_tokens: int,
) -> dict:
    """Detalle de cada etapa para la traza extendida: que entra, con que puntuacion y donde se cae."""
    vector_rank = {row["id"]: rank for rank, row in enumerate(vector_rows, start=1)}
    text_rank = {row["id"]: rank for rank, row in enumerate(text_rows, start=1)}
    fused_rank = {chunk.chunk_id: rank for rank, chunk in enumerate(ordered, start=1)}
    returned_ids = {chunk.chunk_id for chunk in results}

    def chunk_entry(rank: int, chunk: "RetrievedChunk", **extra) -> dict:
        return {
            "rank": rank,
            "chunk_id": chunk.chunk_id,
            "source": f"{chunk.source_type}:{chunk.source_id}",
            "title": _title(chunk.metadata),
            **extra,
        }

    return {
        "vector": _row_stage(vector_rows),
        "bm25": _row_stage(text_rows),
        "fused": [
            chunk_entry(
                rank,
                chunk,
                rrf_score=round(chunk.hybrid_score, 6),
                vector_rank=vector_rank.get(chunk.chunk_id),
                bm25_rank=text_rank.get(chunk.chunk_id),
            )
            for rank, chunk in enumerate(ordered, start=1)
        ],
        "reranked": [
            chunk_entry(rank, chunk, rerank_score=round(chunk.rerank_score, 6), fused_rank=fused_rank.get(chunk.chunk_id))
            for rank, chunk in enumerate((reranked or [])[:reranker_candidates], start=1)
        ],
        "token_budget": {
            "budget_tokens": budget_tokens,
            "top_k_tokens": sum(estimate_tokens(chunk.content) for chunk in top),
            "kept_tokens": sum(estimate_tokens(chunk.content) for chunk in results),
            "dropped": [
                chunk_entry(rank, chunk, tokens=estimate_tokens(chunk.content))
                for rank, chunk in enumerate(top, start=1)
                if chunk.chunk_id not in returned_ids
            ],
        },
        "returned": [
            chunk_entry(
                rank,
                chunk,
                final_score=round(chunk.final_score, 6),
                rerank_score=round(chunk.rerank_score, 6),
                rrf_score=round(chunk.hybrid_score, 6),
                vector_score=round(chunk.vector_score, 6),
                bm25_score=round(chunk.text_score, 6),
                tokens=estimate_tokens(chunk.content),
            )
            for rank, chunk in enumerate(results, start=1)
        ],
    }


def estimate_tokens(text: str) -> int:
    """Estimacion conservadora (~3,2 caracteres por token en espanol) sin depender del tokenizer del modelo."""
    return max(1, int(len(text or "") / 3.2) + 1)


def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    maximum = max(scores)
    minimum = min(scores)
    if maximum == minimum:
        return [1.0 if maximum > 0 else 0.0 for _ in scores]
    return [(score - minimum) / (maximum - minimum) for score in scores]


class HybridRetriever:
    """Replica el flujo de Azure AI Search con ranker semantico:

    1. BM25 (palabras clave, OR) y busqueda vectorial en paralelo, 50 candidatos cada una.
    2. Fusion con Reciprocal Rank Fusion (k=60, mismo peso por defecto).
    3. El reranker reordena los candidatos fusionados (hasta 100 = una "search unit" de Cohere;
       Azure usa los 50 primeros) y su puntuacion es la que manda.
    4. Se devuelven los top_k (16) y se recortan los peores hasta caber en el presupuesto de tokens.
    """

    def __init__(self, session: Session, reranker: RerankerProvider | None = None):
        settings = get_settings()
        self.chunk_repository = ChunkRepository(session)
        self.reranker_candidates = max(1, int(settings.reranker_candidates))
        self.context_max_tokens = max(1, int(settings.retrieval_context_max_tokens))
        self.confidence_threshold = float(settings.retrieval_confidence_threshold)
        self.reranker = reranker if reranker is not None else build_default_reranker()

    def search(
        self,
        query: str,
        query_embedding: list[float],
        limit: int | None = None,
        config: RetrievalConfig | None = None,
        filters: RetrievalFilters | dict | None = None,
        keyword_query: str | None = None,
    ) -> list[RetrievedChunk]:
        effective = (config or DEFAULT_RETRIEVAL_CONFIG).normalized()
        if limit is not None:
            effective = replace(effective, top_k=max(1, int(limit))).normalized()
        requested_filters = normalize_retrieval_filters(filters)
        effective_filters = requested_filters.hard()
        text_query = (keyword_query or "").strip() or query

        with start_span(
            "retrieval.hybrid_search",
            {
                "query.length": len(query),
                "retrieval.keyword_query": text_query,
                "retrieval.top_k": effective.top_k,
                "retrieval.vector_weight": effective.vector_weight,
                "retrieval.text_weight": effective.text_weight,
                "retrieval.vector_candidates": effective.vector_candidates,
                "retrieval.text_candidates": effective.text_candidates,
                "retrieval.rrf_k": effective.rrf_k,
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
            text_rows = self.chunk_repository.text_search(text_query, limit=effective.text_candidates, filters=effective_filters)
            filter_fallback = False

            with start_span(
                "retrieval.merge_rank",
                {
                    "retrieval.vector_candidates_returned": len(vector_rows),
                    "retrieval.text_candidates_returned": len(text_rows),
                },
            ) as merge_span:
                ordered = self._merge_rows(vector_rows, text_rows, effective)
                filtered_attempt = None
                if effective_filters.active() and len(ordered) < effective.top_k:
                    filter_fallback = True
                    filtered_attempt = {
                        "filters": effective_filters.to_dict(),
                        "vector": _row_stage(vector_rows),
                        "bm25": _row_stage(text_rows),
                        "fused_count": len(ordered),
                    }
                    vector_rows = self.chunk_repository.vector_search(query_embedding, limit=effective.vector_candidates)
                    text_rows = self.chunk_repository.text_search(text_query, limit=effective.text_candidates)
                    ordered = self._merge_rows(vector_rows, text_rows, effective)

                rerank_started = time.perf_counter()
                reranked = self._rerank(query, ordered)
                rerank_ms = round((time.perf_counter() - rerank_started) * 1000, 1)
                weak_filtered = (
                    effective_filters.active()
                    and not filter_fallback
                    and reranked
                    and reranked[0].final_score < getattr(self, "confidence_threshold", 0.0)
                )
                if weak_filtered:
                    # Los filtros han dejado resultados poco relevantes: se repite sin filtros y gana el mejor.
                    unfiltered_vector = self.chunk_repository.vector_search(query_embedding, limit=effective.vector_candidates)
                    unfiltered_text = self.chunk_repository.text_search(text_query, limit=effective.text_candidates)
                    unfiltered_ordered = self._merge_rows(unfiltered_vector, unfiltered_text, effective)
                    unfiltered_reranked = self._rerank(query, unfiltered_ordered)
                    if unfiltered_reranked and unfiltered_reranked[0].final_score > reranked[0].final_score:
                        filtered_attempt = {
                            "filters": effective_filters.to_dict(),
                            "reason": "weak_filtered_results",
                            "best_filtered_score": reranked[0].final_score,
                            "vector": _row_stage(vector_rows),
                            "bm25": _row_stage(text_rows),
                            "fused_count": len(ordered),
                        }
                        filter_fallback = True
                        vector_rows, text_rows = unfiltered_vector, unfiltered_text
                        ordered, reranked = unfiltered_ordered, unfiltered_reranked
                ranked = reranked or ordered
                top = ranked[: effective.top_k]
                results = self._fit_token_budget(top)
                if current_recorder() is not None:
                    record_trace_event(
                        "retrieval.hybrid_search",
                        {
                            "query": query,
                            "keyword_query": text_query,
                            "config": {
                                "top_k": effective.top_k,
                                "vector_weight": effective.vector_weight,
                                "text_weight": effective.text_weight,
                                "vector_candidates": effective.vector_candidates,
                                "text_candidates": effective.text_candidates,
                                "rrf_k": effective.rrf_k,
                                "reranker_candidates": getattr(self, "reranker_candidates", 0),
                                "context_max_tokens": getattr(self, "context_max_tokens", 0),
                            },
                            "filters": effective_filters.to_dict(),
                            "planner_filters": requested_filters.to_dict(),
                            "filter_fallback": filter_fallback,
                            "filtered_attempt": filtered_attempt,
                            "reranker": {
                                "enabled": bool(self.reranker),
                                "applied": bool(reranked),
                                "model": getattr(self.reranker, "model", "") if self.reranker else "",
                                "latency_ms": rerank_ms,
                            },
                            "stages": _retrieval_stages(
                                vector_rows=vector_rows,
                                text_rows=text_rows,
                                ordered=ordered,
                                reranked=reranked,
                                reranker_candidates=max(1, int(getattr(self, "reranker_candidates", len(ordered)) or 1)),
                                top=top,
                                results=results,
                                budget_tokens=int(getattr(self, "context_max_tokens", 16000)),
                            ),
                        },
                    )
                self.last_debug = {
                    "keyword_query": text_query,
                    "filter_fallback": filter_fallback,
                    "reranker_applied": bool(reranked),
                    "returned_chunk_ids": [chunk.chunk_id for chunk in results],
                    # Fuentes ("tipo:id") en orden por etapa, para ver donde se pierde la fuente buena.
                    "vector_sources": _source_keys(vector_rows),
                    "text_sources": _source_keys(text_rows),
                    "fused_sources": _source_keys(ordered),
                    "reranked_sources": _source_keys(reranked or []),
                    "returned_sources": _source_keys(results),
                }
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
        """Reciprocal Rank Fusion, como el modo hibrido de Azure AI Search.

        RRF usa posiciones, no puntuaciones, asi que no hace falta normalizar escalas tan distintas
        como coseno y BM25. hybrid_score se escala a [0, 1] dividiendo por el maximo posible.
        """
        merged: dict[int, RetrievedChunk] = {}
        fused: dict[int, float] = {}

        def chunk_for(row: dict) -> RetrievedChunk:
            chunk = merged.get(row["id"])
            if chunk is None:
                chunk = RetrievedChunk(
                    chunk_id=row["id"],
                    source_type=row["source_type"],
                    source_id=row["source_id"],
                    content=row["content"],
                    metadata=row["metadata"] or {},
                )
                merged[row["id"]] = chunk
            return chunk

        for rank, row in enumerate(vector_rows, start=1):
            chunk_for(row).vector_score = float(row["score"] or 0.0)
            fused[row["id"]] = fused.get(row["id"], 0.0) + config.vector_weight / (config.rrf_k + rank)
        for rank, row in enumerate(text_rows, start=1):
            chunk_for(row).text_score = float(row["score"] or 0.0)
            fused[row["id"]] = fused.get(row["id"], 0.0) + config.text_weight / (config.rrf_k + rank)

        best_possible = (config.vector_weight + config.text_weight) / (config.rrf_k + 1)
        for chunk_id, chunk in merged.items():
            chunk.hybrid_score = fused[chunk_id] / best_possible
            chunk.final_score = chunk.hybrid_score

        return sorted(merged.values(), key=lambda item: item.final_score, reverse=True)

    def _fit_token_budget(self, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Quita los peores chunks (los ultimos) hasta que el contexto cabe en el presupuesto."""
        budget = int(getattr(self, "context_max_tokens", 16000))
        kept = list(chunks)
        while len(kept) > 1 and sum(estimate_tokens(chunk.content) for chunk in kept) > budget:
            kept.pop()
        return kept

    def _rerank(self, query: str, ordered: list[RetrievedChunk]) -> list[RetrievedChunk] | None:
        if not self.reranker or not ordered:
            return None

        candidate_limit = max(1, int(getattr(self, "reranker_candidates", len(ordered))))
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
            },
        ) as span:
            try:
                results = self.reranker.rerank(query=query, documents=documents, top_n=len(documents))
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

            # Como el ranker semantico de Azure: el orden final lo decide solo el reranker.
            score_by_id = {item.chunk_id: item.score for item in results}
            for chunk in candidates:
                chunk.rerank_score = score_by_id.get(chunk.chunk_id, 0.0)
                chunk.final_score = chunk.rerank_score
            reranked = sorted(candidates, key=lambda item: item.final_score, reverse=True) + ordered[candidate_limit:]
            set_span_attributes(
                span,
                {
                    "retrieval.reranker.latency_ms": int((time.perf_counter() - started_at) * 1000),
                    "retrieval.reranker.result_count": len(results),
                    "retrieval.reranker.chunk_ids": [item.chunk_id for item in reranked[:16]],
                },
            )
            return reranked
