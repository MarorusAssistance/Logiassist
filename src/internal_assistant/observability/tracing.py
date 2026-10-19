from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from internal_assistant.config import get_settings


logger = logging.getLogger(__name__)
tracer = trace.get_tracer("internal_assistant.rag")
SENSITIVE_KEY_PARTS = ("api_key", "password", "secret", "token", "authorization")
CONTENT_KEY_PARTS = ("content", "prompt", "question", "answer")
CONTENT_EXACT_KEYS = {"message", "query", "text"}


def configure_langsmith_environment(settings=None) -> None:
    current_settings = settings or get_settings()
    enabled = _truthy(getattr(current_settings, "langsmith_tracing", False))
    os.environ.setdefault("LANGSMITH_TRACING", "true" if enabled else "false")
    if not enabled:
        return
    _set_env_if_present("LANGSMITH_API_KEY", getattr(current_settings, "langsmith_api_key", ""))
    _set_env_if_present("LANGSMITH_PROJECT", getattr(current_settings, "langsmith_project", ""))
    _set_env_if_present("LANGSMITH_ENDPOINT", getattr(current_settings, "langsmith_endpoint", ""))


def _set_env_if_present(name: str, value: str | None) -> None:
    if value:
        os.environ.setdefault(name, value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def user_hash(user_id: str | None) -> str:
    if not user_id:
        return ""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def safe_span_attributes(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        normalized_key = key.lower()
        if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
            safe[key] = "<redacted>"
            continue
        if isinstance(value, str) and _looks_like_content_key(normalized_key):
            safe[key] = "<redacted>"
            continue
        safe[key] = _safe_attribute_value(value)
    return safe


def _looks_like_content_key(key: str) -> bool:
    if key.endswith((".length", ".count")):
        return False
    if key in CONTENT_EXACT_KEYS:
        return True
    return any(part in key for part in CONTENT_KEY_PARTS)


def _safe_attribute_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)):
        return value[:500] if isinstance(value, str) else value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes, bytearray)):
        items = list(value)
        if all(isinstance(item, (str, int, float, bool)) for item in items):
            return items[:50]
    return json.dumps(value, default=str, ensure_ascii=False)[:1000]


def set_span_attributes(span: Span, values: dict[str, Any]) -> None:
    for key, value in safe_span_attributes(values).items():
        span.set_attribute(key, value)


@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None):
    with tracer.start_as_current_span(name) as span:
        if attributes:
            set_span_attributes(span, attributes)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def retrieval_span_attributes(
    *,
    retrieved: list[Any],
    confidence: float | None = None,
    prefix: str = "retrieval",
) -> dict[str, Any]:
    scores = [float(getattr(item, "final_score", 0.0) or 0.0) for item in retrieved]
    attrs: dict[str, Any] = {
        f"{prefix}.retrieved_count": len(retrieved),
        f"{prefix}.chunk_ids": [int(getattr(item, "chunk_id")) for item in retrieved],
        f"{prefix}.source_keys": [
            f"{getattr(item, 'source_type')}:{getattr(item, 'source_id')}" for item in retrieved
        ],
        f"{prefix}.score_min": min(scores) if scores else 0.0,
        f"{prefix}.score_max": max(scores) if scores else 0.0,
    }
    if confidence is not None:
        attrs[f"{prefix}.confidence"] = float(confidence)
    return attrs


def serialize_chunks_for_langsmith(chunks: list[Any]) -> list[dict[str, Any]]:
    payload = []
    for item in chunks:
        metadata = dict(getattr(item, "metadata", {}) or {})
        payload.append(
            {
                "chunk_id": getattr(item, "chunk_id", None),
                "source_type": getattr(item, "source_type", None),
                "source_id": getattr(item, "source_id", None),
                "source_key": f"{getattr(item, 'source_type', '')}:{getattr(item, 'source_id', '')}",
                "title": metadata.get("title"),
                "source_url": metadata.get("source_url"),
                "metadata": metadata,
                "vector_score": getattr(item, "vector_score", 0.0),
                "text_score": getattr(item, "text_score", 0.0),
                "hybrid_score": getattr(item, "hybrid_score", 0.0),
                "rerank_score": getattr(item, "rerank_score", 0.0),
                "final_score": getattr(item, "final_score", 0.0),
                "content": getattr(item, "content", ""),
            }
        )
    return payload


def serialize_context_for_langsmith(context_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": item.get("chunk_id"),
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "metadata": item.get("metadata") or {},
            "content": item.get("content", ""),
        }
        for item in context_chunks
    ]


def serialize_decision_for_langsmith(decision: Any) -> dict[str, Any]:
    if hasattr(decision, "model_dump"):
        return decision.model_dump()
    return dict(decision or {})


def _create_dotted_order(start_time: datetime, run_id: UUID) -> str:
    return start_time.strftime("%Y%m%dT%H%M%S%fZ") + str(run_id)


class RagTrace:
    def __init__(self, *, settings=None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self.enabled = _truthy(getattr(self.settings, "langsmith_tracing", False)) and bool(
            getattr(self.settings, "langsmith_api_key", "")
        )
        self.project_name = getattr(self.settings, "langsmith_project", "internal-assistant-mvp")
        self.endpoint = getattr(self.settings, "langsmith_endpoint", "https://api.smith.langchain.com")
        self.client = client if self.enabled else None

    @classmethod
    def from_settings(cls, settings=None) -> "RagTrace":
        return cls(settings=settings)

    def _client(self):
        if not self.enabled:
            return None
        if self.client is not None:
            return self.client
        try:
            from langsmith import Client
        except ImportError:
            logger.warning("LANGSMITH_TRACING esta activo, pero langsmith no esta instalado")
            self.enabled = False
            return None
        self.client = Client(
            api_key=getattr(self.settings, "langsmith_api_key", ""),
            api_url=self.endpoint,
        )
        return self.client

    def start_run(
        self,
        name: str,
        *,
        run_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_run_id: UUID | None = None,
        trace_id: UUID | None = None,
        parent_dotted_order: str | None = None,
    ) -> "LangSmithRun":
        return LangSmithRun(
            trace=self,
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            metadata=metadata or {},
            parent_run_id=parent_run_id,
            trace_id=trace_id,
            parent_dotted_order=parent_dotted_order,
        )


class LangSmithRun:
    def __init__(
        self,
        *,
        trace: RagTrace,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any],
        parent_run_id: UUID | None = None,
        trace_id: UUID | None = None,
        parent_dotted_order: str | None = None,
    ) -> None:
        self.trace = trace
        self.name = name
        self.run_type = run_type
        self.inputs = inputs
        self.metadata = metadata
        self.run_id = uuid4()
        self.parent_run_id = parent_run_id
        self.trace_id = trace_id or self.run_id
        self.parent_dotted_order = parent_dotted_order
        self.dotted_order: str | None = None
        self.start_time: datetime | None = None
        self.outputs: dict[str, Any] | None = None
        self.created = False

    def __enter__(self) -> "LangSmithRun":
        client = self.trace._client()
        if not client:
            return self
        try:
            self.start_time = datetime.now(timezone.utc)
            current_dotted_order = _create_dotted_order(self.start_time, self.run_id)
            self.dotted_order = (
                f"{self.parent_dotted_order}.{current_dotted_order}"
                if self.parent_dotted_order
                else current_dotted_order
            )
            client.create_run(
                id=self.run_id,
                trace_id=self.trace_id,
                parent_run_id=self.parent_run_id,
                dotted_order=self.dotted_order,
                name=self.name,
                inputs=self.inputs,
                run_type=self.run_type,
                project_name=self.trace.project_name,
                start_time=self.start_time,
                extra={"metadata": self.metadata},
            )
            self.created = True
        except Exception as exc:
            logger.warning("No se pudo crear run de LangSmith %s: %s", self.name, exc)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        client = self.trace._client()
        if not client or not self.created:
            return
        try:
            client.update_run(
                self.run_id,
                end_time=datetime.now(timezone.utc),
                outputs=self.outputs or {},
                error=str(exc) if exc else None,
            )
        except Exception as update_exc:
            logger.warning("No se pudo cerrar run de LangSmith %s: %s", self.name, update_exc)

    def child(
        self,
        name: str,
        *,
        run_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "LangSmithRun":
        return self.trace.start_run(
            name,
            run_type=run_type,
            inputs=inputs or {},
            metadata=metadata or {},
            parent_run_id=self.run_id,
            trace_id=self.trace_id,
            parent_dotted_order=self.dotted_order,
        )

    def set_outputs(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs
