"""Trazas extendidas por peticion, guardadas como un JSON por request en un storage.

LangSmith y Application Insights recortan contenido (y AI redacta el texto). Para depurar la busqueda
hace falta todo: la peticion, el plan del planificador, los candidatos de cada etapa (vector, BM25,
fusion, reranker, presupuesto de tokens), la decision del filtro de evidencia, las llamadas al LLM
con su salida cruda y el uso de tokens.

Cada peticion genera un documento en:

    {cliente}/{proyecto}/{conversacion}/{AAAAMMDDTHHMMSS}_{request_id}.json

- TRACE_STORE=local      -> carpeta TRACE_STORE_DIR (por defecto `traces/`, ignorada por git).
- TRACE_STORE=azure_blob -> contenedor TRACE_STORE_CONTAINER de la cuenta TRACE_STORE_ACCOUNT_URL
                            (identidad administrada) o TRACE_STORE_CONNECTION_STRING.
- TRACE_STORE=none       -> desactivado.

La escritura va en segundo plano y nunca rompe una peticion: si falla, se registra un aviso.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

from internal_assistant.config import get_settings


logger = logging.getLogger(__name__)

TRACE_SCHEMA_VERSION = 1
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _segment(value: Any, default: str) -> str:
    cleaned = _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip("-.")
    return cleaned[:80] or default


def _jsonable(value: Any) -> Any:
    """Convierte dataclasses, modelos pydantic y objetos con to_dict a JSON serializable."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {name: getattr(value, name) for name in value.__dataclass_fields__}
    return str(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TraceRecorder:
    """Acumula los pasos de una peticion y produce el documento de traza."""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    _t0: float = field(default_factory=time.perf_counter)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    def add_run(self, run: dict[str, Any]) -> None:
        with self._lock:
            self.runs.append(run)

    def add_event(self, name: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.events.append({"name": name, "at_ms": self.elapsed_ms(), **payload})

    def update_metadata(self, **values: Any) -> None:
        with self._lock:
            self.metadata.update({key: value for key, value in values.items() if value is not None})

    def build_document(self, *, inputs: dict[str, Any], outputs: dict[str, Any] | None, error: str | None, usage: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            runs = sorted(self.runs, key=lambda item: item.get("start_ms", 0.0))
            events = list(self.events)
            metadata = dict(self.metadata)
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "request_id": self.request_id,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.elapsed_ms(),
            "metadata": metadata,
            "inputs": inputs,
            "outputs": outputs or {},
            "error": error,
            "usage": usage,
            "runs": runs,
            "events": events,
        }


_ACTIVE_RECORDER: ContextVar[TraceRecorder | None] = ContextVar("internal_assistant_trace_recorder", default=None)


def current_recorder() -> TraceRecorder | None:
    return _ACTIVE_RECORDER.get()


def activate_recorder(recorder: TraceRecorder):
    return _ACTIVE_RECORDER.set(recorder)


def deactivate_recorder(token) -> None:
    _ACTIVE_RECORDER.reset(token)


def record_trace_event(name: str, payload: dict[str, Any]) -> None:
    """Anade un evento detallado a la traza de la peticion actual (no-op si no hay ninguna)."""
    recorder = _ACTIVE_RECORDER.get()
    if recorder is None:
        return
    try:
        recorder.add_event(name, payload)
    except Exception as exc:  # pragma: no cover - la traza nunca debe romper la peticion
        logger.warning("No se pudo registrar el evento de traza %s: %s", name, exc)


class TraceSink(Protocol):
    def write(self, path: str, document: dict[str, Any]) -> None: ...


class LocalTraceSink:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def write(self, path: str, document: dict[str, Any]) -> None:
        target = self.base_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")


class AzureBlobTraceSink:
    def __init__(self, *, container: str, account_url: str = "", connection_string: str = "") -> None:
        from azure.storage.blob import BlobServiceClient, ContentSettings

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            from azure.identity import DefaultAzureCredential

            service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        self._container = service.get_container_client(container)
        self._content_settings = ContentSettings(content_type="application/json; charset=utf-8")

    def write(self, path: str, document: dict[str, Any]) -> None:
        data = json.dumps(document, ensure_ascii=False, default=_jsonable).encode("utf-8")
        self._container.upload_blob(path, data, overwrite=True, content_settings=self._content_settings)


_SINK_LOCK = threading.Lock()
_SINK_CACHE: dict[tuple, TraceSink | None] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="trace-store")


def build_trace_sink(settings=None) -> TraceSink | None:
    current = settings or get_settings()
    kind = str(getattr(current, "trace_store", "none") or "none").strip().lower()
    if kind in {"", "none", "off", "false", "disabled"}:
        return None
    key = (
        kind,
        getattr(current, "trace_store_dir", ""),
        getattr(current, "trace_store_container", ""),
        getattr(current, "trace_store_account_url", ""),
        bool(getattr(current, "trace_store_connection_string", "")),
    )
    with _SINK_LOCK:
        if key in _SINK_CACHE:
            return _SINK_CACHE[key]
        sink: TraceSink | None = None
        try:
            if kind == "local":
                sink = LocalTraceSink(getattr(current, "trace_store_dir", "") or "traces")
            elif kind in {"azure_blob", "blob", "azure"}:
                sink = AzureBlobTraceSink(
                    container=getattr(current, "trace_store_container", "") or "rag-traces",
                    account_url=getattr(current, "trace_store_account_url", ""),
                    connection_string=getattr(current, "trace_store_connection_string", ""),
                )
            else:
                logger.warning("TRACE_STORE=%s no reconocido; trazas extendidas desactivadas", kind)
        except Exception as exc:
            logger.warning("No se pudo inicializar el almacen de trazas (%s): %s", kind, exc)
            sink = None
        _SINK_CACHE[key] = sink
        return sink


def trace_path(settings, recorder: TraceRecorder) -> str:
    current = settings or get_settings()
    metadata = recorder.metadata
    client = _segment(getattr(current, "trace_client", ""), "default-client")
    project = _segment(getattr(current, "trace_project", "") or getattr(current, "app_name", ""), "default-project")
    conversation_id = metadata.get("conversation_id")
    if metadata.get("mode") == "simulation":
        conversation = _segment(metadata.get("eval_scenario_id") or f"simulation-{conversation_id or 0}", "simulation")
        project = f"{project}/eval"
    else:
        conversation = _segment(f"conversation-{conversation_id}" if conversation_id else "", "no-conversation")
    stamp = recorder.started_at.strftime("%Y%m%dT%H%M%S")
    return f"{client}/{project}/{conversation}/{stamp}_{recorder.request_id}.json"


def store_trace(document: dict[str, Any], path: str, sink: TraceSink, *, wait: bool = False) -> None:
    def _write() -> None:
        try:
            sink.write(path, document)
        except Exception as exc:
            logger.warning("No se pudo guardar la traza %s: %s", path, exc)

    if wait:
        _write()
    else:
        _EXECUTOR.submit(_write)


def record_llm_call(
    *,
    purpose: str,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    raw_output: str | None,
    usage: Any = None,
    latency_ms: float | None = None,
    finish_reason: str | None = None,
) -> None:
    """Guarda el prompt completo y la salida cruda del modelo (antes de parsear) en la traza actual."""
    if _ACTIVE_RECORDER.get() is None:
        return
    if usage is not None and not isinstance(usage, dict):
        usage = usage.model_dump() if hasattr(usage, "model_dump") else _jsonable(usage)
    record_trace_event(
        "llm.call",
        {
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "usage": usage,
            "messages": messages,
            "raw_output": raw_output,
        },
    )
