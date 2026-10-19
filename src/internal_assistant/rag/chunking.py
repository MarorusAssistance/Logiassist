from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(slots=True)
class ChunkPayload:
    chunk_index: int
    content: str
    content_hash: str
    metadata: dict


def _split_paragraphs(text: str) -> list[str]:
    parts = [part.strip() for part in text.split("\n") if part.strip()]
    return parts or [text.strip()]


def _chunk_text(text: str, metadata: dict, target_size: int = 900, overlap: int = 150) -> list[ChunkPayload]:
    paragraphs = _split_paragraphs(text)
    chunks: list[ChunkPayload] = []
    buffer = ""
    chunk_index = 0

    for paragraph in paragraphs:
        candidate = f"{buffer}\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= target_size:
            buffer = candidate
            continue

        if buffer:
            chunks.append(
                ChunkPayload(
                    chunk_index=chunk_index,
                    content=buffer,
                    content_hash=sha256(buffer.encode("utf-8")).hexdigest(),
                    metadata=metadata,
                )
            )
            chunk_index += 1
            tail = buffer[-overlap:] if len(buffer) > overlap else buffer
            buffer = f"{tail}\n{paragraph}".strip()
        else:
            chunks.append(
                ChunkPayload(
                    chunk_index=chunk_index,
                    content=paragraph[:target_size],
                    content_hash=sha256(paragraph[:target_size].encode("utf-8")).hexdigest(),
                    metadata=metadata,
                )
            )
            chunk_index += 1
            buffer = paragraph[target_size - overlap :]

    if buffer:
        chunks.append(
            ChunkPayload(
                chunk_index=chunk_index,
                content=buffer,
                content_hash=sha256(buffer.encode("utf-8")).hexdigest(),
                metadata=metadata,
            )
        )

    return chunks


def build_chunks_for_document(document) -> list[ChunkPayload]:
    metadata = {
        "title": document.title,
        "document_type": getattr(document, "document_type", None),
        "department": document.department,
        "affected_system": document.affected_system,
        "tags": document.tags,
        "source_url": document.source_url,
    }
    return _chunk_text(document.content, metadata=metadata)


def build_chunks_for_incident(incident) -> list[ChunkPayload]:
    body = "\n".join(
        [
            f"Titulo: {incident.title}",
            f"Descripcion: {incident.description}",
            f"Sistema: {incident.affected_system}",
            f"Estado: {incident.status}",
            f"Resolucion: {incident.resolution or ''}",
            f"Impacto: {incident.impact or ''}",
            f"Comportamiento esperado: {incident.expected_behavior or ''}",
            f"Comportamiento actual: {incident.actual_behavior or ''}",
        ]
    ).strip()
    metadata = {
        "title": incident.title,
        "department": incident.department,
        "affected_system": incident.affected_system,
        "status": incident.status,
        "tags": incident.tags,
        "source_url": incident.source_url,
        "is_resolved": incident.is_resolved,
    }
    if len(body) <= 900:
        return [
            ChunkPayload(
                chunk_index=0,
                content=body,
                content_hash=sha256(body.encode("utf-8")).hexdigest(),
                metadata=metadata,
            )
        ]
    return _chunk_text(body, metadata=metadata)
