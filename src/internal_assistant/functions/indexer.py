from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from internal_assistant.config import get_settings
from internal_assistant.llm import LLMProvider, MockLLMProvider, build_default_provider, normalize_provider_name, resolve_provider_name
from internal_assistant.models import Chunk, Document, Incident
from internal_assistant.rag.chunking import build_chunks_for_document, build_chunks_for_incident
from internal_assistant.repositories.retrieval import ChunkRepository


def _refresh_tsvector(session: Session, chunk_id: int) -> None:
    session.execute(
        text("UPDATE chunks SET full_text_tsvector = to_tsvector('spanish', content) WHERE id = :chunk_id"),
        {"chunk_id": chunk_id},
    )


def _resolve_indexing_provider(llm_provider: LLMProvider | None = None) -> LLMProvider:
    if llm_provider is not None:
        return llm_provider

    settings = get_settings()
    embeddings_provider = normalize_provider_name(settings.embeddings_provider or "")
    chat_provider = resolve_provider_name(settings)

    if embeddings_provider in {"", "auto", chat_provider}:
        return build_default_provider()

    if chat_provider == "openai_compatible" and embeddings_provider == "openai":
        return build_default_provider()

    if embeddings_provider == "mock" and chat_provider == "mock":
        return MockLLMProvider()

    raise ValueError(
        "EMBEDDINGS_PROVIDER no es compatible con el proveedor de chat configurado. "
        "Usa el mismo proveedor, el modo mock completo, o LLM_PROVIDER=openai_compatible con EMBEDDINGS_PROVIDER=openai."
    )


def _count_result(session: Session, statement) -> int:
    return int(session.execute(statement).scalar_one())


def _embedding_dimensions(session: Session) -> int:
    result = session.execute(text("SELECT COALESCE(MAX(vector_dims(embedding)), 0) FROM chunks WHERE embedding IS NOT NULL"))
    return int(result.scalar_one())


def _vector_extension_enabled(session: Session) -> bool:
    result = session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
    return bool(result.scalar_one())


def _index_source_chunks(session: Session, source_type: str, source_id: int, payloads: Iterable, llm_provider: LLMProvider) -> int:
    payload_list = list(payloads)
    repository = ChunkRepository(session)
    repository.delete_by_source(source_type, source_id)
    embeddings = llm_provider.embed_texts([payload.content for payload in payload_list]) if payload_list else []
    if len(embeddings) != len(payload_list):
        raise ValueError("El proveedor devolvio un numero de embeddings distinto al numero de chunks")

    for payload, embedding in zip(payload_list, embeddings, strict=True):
        metadata = payload.metadata or {}
        chunk = Chunk(
            source_type=source_type,
            source_id=source_id,
            chunk_index=payload.chunk_index,
            content=payload.content,
            content_hash=payload.content_hash,
            embedding=embedding,
            metadata_=metadata,
            source_title=metadata.get("title"),
            affected_system=metadata.get("affected_system"),
            department=metadata.get("department"),
            document_type=metadata.get("document_type") if source_type == "document" else None,
            incident_status=metadata.get("status") if source_type == "incident" else None,
            is_resolved=metadata.get("is_resolved") if source_type == "incident" else None,
            tags=metadata.get("tags") or [],
            full_text_tsvector=None,
        )
        persisted = repository.upsert(chunk)
        _refresh_tsvector(session, persisted.id)

    session.commit()
    return len(payload_list)


def index_document(session: Session, document_id: int, llm_provider: LLMProvider | None = None) -> int:
    provider = _resolve_indexing_provider(llm_provider)
    document = session.get(Document, document_id)
    if not document:
        return 0
    payloads = build_chunks_for_document(document)
    return _index_source_chunks(session, "document", document.id, payloads, provider)


def index_incident(session: Session, incident_id: int, llm_provider: LLMProvider | None = None) -> int:
    provider = _resolve_indexing_provider(llm_provider)
    incident = session.get(Incident, incident_id)
    if not incident:
        return 0
    payloads = build_chunks_for_incident(incident)
    return _index_source_chunks(session, "incident", incident.id, payloads, provider)


def rebuild_index(session: Session, llm_provider: LLMProvider | None = None) -> dict:
    provider = _resolve_indexing_provider(llm_provider)
    document_ids = session.execute(select(Document.id).order_by(Document.id)).scalars().all()
    incident_ids = session.execute(select(Incident.id).order_by(Incident.id)).scalars().all()

    session.execute(text("DELETE FROM chunks"))
    session.commit()

    document_chunk_count = 0
    incident_chunk_count = 0

    for document_id in document_ids:
        document_chunk_count += index_document(session, document_id, provider)

    for incident_id in incident_ids:
        incident_chunk_count += index_incident(session, incident_id, provider)

    total_chunks = _count_result(session, select(func.count(Chunk.id)))
    chunks_with_embeddings = _count_result(session, select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None)))
    return {
        "incidents_read": len(incident_ids),
        "documents_read": len(document_ids),
        "incidents_chunks": incident_chunk_count,
        "documents_chunks": document_chunk_count,
        "total_chunks": total_chunks,
        "chunks_with_embeddings": chunks_with_embeddings,
        "embedding_dimensions": _embedding_dimensions(session),
        "vector_extension_enabled": _vector_extension_enabled(session),
    }


def check_index(session: Session, llm_provider: LLMProvider | None = None) -> dict:
    incidents_count = _count_result(session, select(func.count(Incident.id)))
    documents_count = _count_result(session, select(func.count(Document.id)))
    chunks_count = _count_result(session, select(func.count(Chunk.id)))
    chunks_with_embeddings = _count_result(session, select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None)))
    dimensions = _embedding_dimensions(session)
    expected_dimensions = get_settings().embedding_dimensions
    vector_extension_enabled = _vector_extension_enabled(session)

    if incidents_count == 0:
        raise ValueError("No hay incidents cargados en PostgreSQL")
    if documents_count == 0:
        raise ValueError("No hay documents cargados en PostgreSQL")
    if chunks_count == 0:
        raise ValueError("No hay chunks en el indice")
    if chunks_with_embeddings == 0:
        raise ValueError("No hay chunks con embeddings")
    if dimensions != expected_dimensions:
        raise ValueError(
            f"La dimension de embeddings no coincide: se esperaba {expected_dimensions} y se obtuvo {dimensions}"
        )
    if not vector_extension_enabled:
        raise ValueError("La extension vector no esta habilitada en PostgreSQL")

    vector_results = (
        session.execute(
            text(
                """
                SELECT id, source_type, source_id, content, metadata, 1 - (embedding <=> embedding) AS score
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> embedding
                LIMIT 5
                """
            )
        )
        .mappings()
        .all()
    )
    if not vector_results:
        raise ValueError("La busqueda vectorial no devolvio resultados")

    text_query = "acceso temporal SafeGate"
    repository = ChunkRepository(session)
    text_results = repository.text_search(text_query, limit=5)
    if not text_results:
        raise ValueError("La busqueda full-text no devolvio resultados")

    return {
        "incidents": incidents_count,
        "documents": documents_count,
        "chunks": chunks_count,
        "chunks_with_embeddings": chunks_with_embeddings,
        "embedding_dimensions": dimensions,
        "vector_extension_enabled": vector_extension_enabled,
        "vector_results": [dict(row) for row in vector_results],
        "text_results": text_results,
    }
