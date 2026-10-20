from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from internal_assistant.config import get_settings
from internal_assistant.observability.usage import record_usage


logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 503}
MAX_RERANK_ATTEMPTS = 6


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    try:
        return min(60.0, max(1.0, float(retry_after)))
    except (TypeError, ValueError):
        return min(60.0, 2.0 * (2**attempt))


def _post_with_retry(url: str, **kwargs) -> httpx.Response:
    """Reintenta 429/503 (cuotas por minuto pequenas en Foundry) respetando Retry-After."""
    for attempt in range(MAX_RERANK_ATTEMPTS):
        response = httpx.post(url, **kwargs)
        if response.status_code not in RETRYABLE_STATUS or attempt == MAX_RERANK_ATTEMPTS - 1:
            return response
        time.sleep(_retry_delay(response, attempt))
    return response


@dataclass(frozen=True, slots=True)
class RerankResult:
    chunk_id: int
    score: float


class RerankerProvider(Protocol):
    model: str

    def rerank(self, *, query: str, documents: list[dict], top_n: int) -> list[RerankResult]:
        ...


class HttpRerankerProvider:
    provider_name = "http"

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def rerank(self, *, query: str, documents: list[dict], top_n: int) -> list[RerankResult]:
        response = _post_with_retry(
            f"{self.base_url}/rerank",
            json={"query": query, "documents": documents, "top_n": top_n},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        # Reranker local (BGE): se registra la peticion; su coste es 0.
        record_usage(component="reranker", purpose="rerank", provider=self.provider_name, model=self.model, search_units=0)
        results = []
        for item in payload.get("results") or []:
            results.append(RerankResult(chunk_id=int(item["id"]), score=float(item["score"])))
        return results


def _document_text(document: dict) -> str:
    """Texto que ve el reranker: metadatos utiles (titulo, sistema, tipo) mas el contenido del chunk."""
    metadata = document.get("metadata") or {}
    return "\n".join(
        item
        for item in [
            f"title: {metadata.get('source_title') or metadata.get('title') or ''}",
            f"source_type: {metadata.get('source_type') or ''}",
            f"source_id: {metadata.get('source_id') or ''}",
            f"affected_system: {metadata.get('affected_system') or ''}",
            f"department: {metadata.get('department') or ''}",
            f"document_type: {metadata.get('document_type') or ''}",
            f"content: {document.get('text') or ''}",
        ]
        if item.split(": ", 1)[-1]
    )


class VoyageRerankerProvider:
    """Voyage AI (rerank-3 / rerank-3-lite) a traves de la API de MongoDB Atlas.

    Con una clave de geografia Europa y el endpoint `eu.ai.mongodb.com` la inferencia se hace en la UE.
    Factura por tokens: tokens_consulta x n_documentos + tokens de todos los documentos.
    """

    provider_name = "voyage"

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "rerank-3-lite"
        self.timeout_seconds = timeout_seconds

    @property
    def endpoint_url(self) -> str:
        if self.base_url.endswith("/rerank"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/rerank"
        return f"{self.base_url}/v1/rerank"

    def rerank(self, *, query: str, documents: list[dict], top_n: int) -> list[RerankResult]:
        chunk_ids = [int(document["id"]) for document in documents]
        response = _post_with_retry(
            self.endpoint_url,
            json={
                "model": self.model,
                "query": query,
                "documents": [_document_text(document) for document in documents],
                "top_k": top_n,
                "truncation": True,
            },
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage") or {}
        record_usage(
            component="reranker",
            purpose="rerank",
            provider=self.provider_name,
            model=self.model,
            input_tokens=usage.get("total_tokens") or payload.get("total_tokens") or 0,
        )
        results = []
        for item in payload.get("data") or payload.get("results") or []:
            index = int(item["index"])
            if 0 <= index < len(chunk_ids):
                results.append(RerankResult(chunk_id=chunk_ids[index], score=float(item.get("relevance_score", 0.0))))
        return results


class CohereAzureRerankerProvider:
    provider_name = "cohere_azure"

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model or "model"
        self.timeout_seconds = timeout_seconds

    @property
    def endpoint_url(self) -> str:
        if self.base_url.endswith("/rerank"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/rerank"
        return f"{self.base_url}/v1/rerank"

    def rerank(self, *, query: str, documents: list[dict], top_n: int) -> list[RerankResult]:
        chunk_ids = [int(document["id"]) for document in documents]
        cohere_documents = [_document_text(document) for document in documents]

        response = _post_with_retry(
            self.endpoint_url,
            json={
                "model": self.model,
                "query": query,
                "documents": cohere_documents,
                "top_n": top_n,
            },
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code in {401, 403}:
            response = _post_with_retry(
                self.endpoint_url,
                json={
                    "model": self.model,
                    "query": query,
                    "documents": cohere_documents,
                    "top_n": top_n,
                },
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
        record_usage(
            component="reranker",
            purpose="rerank",
            provider=self.provider_name,
            model=self.model,
            search_units=_billed_search_units(payload),
        )
        results = []
        for item in payload.get("results") or []:
            index = int(item["index"])
            if index < 0 or index >= len(chunk_ids):
                continue
            score = item.get("relevance_score", item.get("score", 0.0))
            results.append(RerankResult(chunk_id=chunk_ids[index], score=float(score)))
        return results


def _billed_search_units(payload: dict) -> float:
    """Cohere factura por search units (`meta.billed_units.search_units`); si no viene, 1 por peticion."""
    meta = payload.get("meta") if isinstance(payload, dict) else None
    billed = (meta or {}).get("billed_units") or {}
    try:
        units = float(billed.get("search_units") or 0)
    except (TypeError, ValueError):
        units = 0.0
    return units if units > 0 else 1.0


def build_default_reranker() -> RerankerProvider | None:
    settings = get_settings()
    if not settings.reranker_enabled:
        return None
    provider = settings.reranker_provider.strip().lower()
    if provider in {"voyage", "voyageai", "voyage_atlas"}:
        api_key = settings.voyage_api_key or settings.reranker_api_key
        if not api_key:
            logger.warning("RERANKER_PROVIDER=voyage but VOYAGE_API_KEY is empty; reranker disabled")
            return None
        return VoyageRerankerProvider(
            base_url=settings.voyage_base_url,
            api_key=api_key,
            model=settings.voyage_rerank_model,
            timeout_seconds=settings.reranker_timeout_seconds,
        )
    if provider in {"cohere_azure", "azure_cohere", "cohere"}:
        if not settings.reranker_api_key:
            logger.warning("RERANKER_PROVIDER=%s but RERANKER_API_KEY is empty; reranker disabled", provider)
            return None
        return CohereAzureRerankerProvider(
            base_url=settings.reranker_base_url,
            api_key=settings.reranker_api_key,
            model=settings.reranker_model,
            timeout_seconds=settings.reranker_timeout_seconds,
        )
    return HttpRerankerProvider(
        base_url=settings.reranker_base_url,
        model=settings.reranker_model,
        timeout_seconds=settings.reranker_timeout_seconds,
    )
