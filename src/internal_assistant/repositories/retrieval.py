from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from internal_assistant.models import Chunk, RetrievalLog
from internal_assistant.observability.tracing import set_span_attributes, start_span
from internal_assistant.rag.filters import RetrievalFilters, normalize_retrieval_filters


def _chunk_select_columns() -> str:
    return """
        id, source_type, source_id, content, metadata,
        source_title, affected_system, department, document_type,
        incident_status, is_resolved, tags
    """


def _filters_sql(filters: RetrievalFilters | dict | None) -> tuple[str, dict]:
    normalized = normalize_retrieval_filters(filters)
    clauses: list[str] = []
    params: dict = {}

    if normalized.source_types:
        clauses.append("source_type = ANY(:source_types)")
        params["source_types"] = normalized.source_types
    if normalized.affected_systems:
        clauses.append("affected_system = ANY(:affected_systems)")
        params["affected_systems"] = normalized.affected_systems
    if normalized.departments:
        clauses.append("department = ANY(:departments)")
        params["departments"] = normalized.departments
    if normalized.document_types:
        clauses.append("document_type = ANY(:document_types)")
        params["document_types"] = normalized.document_types
    if normalized.incident_statuses:
        clauses.append("incident_status = ANY(:incident_statuses)")
        params["incident_statuses"] = normalized.incident_statuses
    if normalized.is_resolved is not None:
        clauses.append("is_resolved = :is_resolved")
        params["is_resolved"] = normalized.is_resolved
    if normalized.tags_any:
        clauses.append("tags && :tags_any")
        params["tags_any"] = normalized.tags_any

    return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)


def _bind_filter_params(statement, params: dict):
    for name in (
        "source_types",
        "affected_systems",
        "departments",
        "document_types",
        "incident_statuses",
        "tags_any",
    ):
        if name in params:
            statement = statement.bindparams(bindparam(name, type_=ARRAY(String())))
    return statement


class ChunkRepository:
    def __init__(self, session: Session):
        self.session = session

    def delete_by_source(self, source_type: str, source_id: int) -> None:
        self.session.query(Chunk).filter(Chunk.source_type == source_type, Chunk.source_id == source_id).delete()

    def upsert(self, chunk: Chunk) -> Chunk:
        existing = self.session.query(Chunk).filter(Chunk.content_hash == chunk.content_hash).one_or_none()
        if existing:
            existing.content = chunk.content
            existing.embedding = chunk.embedding
            existing.metadata_ = chunk.metadata_
            existing.full_text_tsvector = chunk.full_text_tsvector
            existing.source_title = chunk.source_title
            existing.affected_system = chunk.affected_system
            existing.department = chunk.department
            existing.document_type = chunk.document_type
            existing.incident_status = chunk.incident_status
            existing.is_resolved = chunk.is_resolved
            existing.tags = chunk.tags
            self.session.add(existing)
            self.session.flush()
            return existing

        self.session.add(chunk)
        self.session.flush()
        return chunk

    def vector_search(self, embedding: list[float], limit: int = 15, filters: RetrievalFilters | dict | None = None) -> list[dict]:
        filter_sql, filter_params = _filters_sql(filters)
        with start_span(
            "retrieval.vector_search",
            {
                "retrieval.vector.limit": limit,
                "retrieval.embedding_dimensions": len(embedding),
                "retrieval.filters.active": bool(filter_sql),
            },
        ) as span:
            stmt = text(
                f"""
                SELECT {_chunk_select_columns()}, 1 - (embedding <=> CAST(:embedding AS vector)) AS score
                FROM chunks
                WHERE embedding IS NOT NULL
                {filter_sql}
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            )
            stmt = _bind_filter_params(stmt, filter_params)
            rows = self.session.execute(stmt, {"embedding": embedding, "limit": limit, **filter_params}).mappings().all()
            payload = [dict(row) for row in rows]
            scores = [float(row.get("score") or 0.0) for row in payload]
            set_span_attributes(
                span,
                {
                    "retrieval.vector.result_count": len(payload),
                    "retrieval.vector.chunk_ids": [row["id"] for row in payload],
                    "retrieval.vector.score_min": min(scores) if scores else 0.0,
                    "retrieval.vector.score_max": max(scores) if scores else 0.0,
                },
            )
            return payload

    def text_search(self, query: str, limit: int = 15, filters: RetrievalFilters | dict | None = None) -> list[dict]:
        filter_sql, filter_params = _filters_sql(filters)
        with start_span(
            "retrieval.text_search",
            {"retrieval.text.limit": limit, "query.length": len(query), "retrieval.filters.active": bool(filter_sql)},
        ) as span:
            stmt = text(
                f"""
                SELECT {_chunk_select_columns()},
                       ts_rank_cd(full_text_tsvector, plainto_tsquery('spanish', :query)) AS score
                FROM chunks
                WHERE full_text_tsvector @@ plainto_tsquery('spanish', :query)
                {filter_sql}
                ORDER BY score DESC
                LIMIT :limit
                """
            )
            stmt = _bind_filter_params(stmt, filter_params)
            rows = self.session.execute(stmt, {"query": query, "limit": limit, **filter_params}).mappings().all()
            payload = [dict(row) for row in rows]
            scores = [float(row.get("score") or 0.0) for row in payload]
            set_span_attributes(
                span,
                {
                    "retrieval.text.result_count": len(payload),
                    "retrieval.text.chunk_ids": [row["id"] for row in payload],
                    "retrieval.text.score_min": min(scores) if scores else 0.0,
                    "retrieval.text.score_max": max(scores) if scores else 0.0,
                },
            )
            return payload


class RetrievalLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        conversation_id: int | None,
        message_id: int | None,
        query: str,
        detected_intent: str,
        retrieved_chunk_ids: Iterable[int],
        retrieved_source_ids: Iterable[int],
        scores: dict,
        confidence_score: float,
        was_answered: bool,
        tokens_input_estimated: int,
        tokens_output_estimated: int,
        latency_ms: int,
        created_ticket_id: int | None = None,
        answer: str | None = None,
    ) -> RetrievalLog:
        log = RetrievalLog(
            conversation_id=conversation_id,
            message_id=message_id,
            query=query,
            detected_intent=detected_intent,
            retrieved_chunk_ids=list(retrieved_chunk_ids),
            retrieved_source_ids=list(retrieved_source_ids),
            scores=scores,
            confidence_score=confidence_score,
            was_answered=was_answered,
            tokens_input_estimated=tokens_input_estimated,
            tokens_output_estimated=tokens_output_estimated,
            latency_ms=latency_ms,
            created_ticket_id=created_ticket_id,
            answer=answer[:1000] if answer else None,
        )
        self.session.add(log)
        self.session.flush()
        return log
