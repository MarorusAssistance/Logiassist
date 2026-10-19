"""Medicion de uso real (tokens, search units) de LLM, embeddings y reranker.

El medidor solo registra eventos cuando hay uno activo en el contexto actual
(``with metering() as meter:``). En produccion no se activa ninguno, por lo que
``record_usage`` es un no-op barato.

Los ``contextvars`` no se propagan de forma fiable a hilos de un
``ThreadPoolExecutor``: cada worker debe abrir su propio ``metering(propagate=False)``
y devolver el medidor para que el hilo principal lo combine con ``merge``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
import threading
from typing import Any


COMPONENTS = ("chat", "embeddings", "reranker")


@dataclass(frozen=True, slots=True)
class UsageEvent:
    component: str
    purpose: str
    provider: str
    model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    search_units: float = 0.0
    requests: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[UsageEvent] = []

    def record(self, event: UsageEvent) -> None:
        with self._lock:
            self._events.append(event)

    def merge(self, other: "UsageMeter | None") -> None:
        if other is None or other is self:
            return
        events = other.events
        with self._lock:
            self._events.extend(events)

    @property
    def events(self) -> list[UsageEvent]:
        with self._lock:
            return list(self._events)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def aggregate(self) -> list[dict[str, Any]]:
        """Agrupa eventos por (component, purpose, provider, model)."""
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for event in self.events:
            key = (event.component, event.purpose, event.provider, event.model)
            row = grouped.setdefault(
                key,
                {
                    "component": event.component,
                    "purpose": event.purpose,
                    "provider": event.provider,
                    "model": event.model,
                    "requests": 0,
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "search_units": 0.0,
                },
            )
            row["requests"] += event.requests
            row["input_tokens"] += event.input_tokens
            row["cached_input_tokens"] += event.cached_input_tokens
            row["output_tokens"] += event.output_tokens
            row["search_units"] += event.search_units
        return [grouped[key] for key in sorted(grouped)]


_ACTIVE_METER: ContextVar[UsageMeter | None] = ContextVar("internal_assistant_usage_meter", default=None)


def current_meter() -> UsageMeter | None:
    return _ACTIVE_METER.get()


@contextmanager
def metering(meter: UsageMeter | None = None, *, propagate: bool = True) -> Iterator[UsageMeter]:
    """Activa un medidor en el contexto actual.

    Con ``propagate=True`` los eventos se suman al medidor que estuviera activo
    al entrar (anidado), de modo que un medidor por turno alimenta al de la
    ejecucion completa sin contar dos veces.
    """
    active = meter or UsageMeter()
    parent = _ACTIVE_METER.get()
    token = _ACTIVE_METER.set(active)
    try:
        yield active
    finally:
        _ACTIVE_METER.reset(token)
        if propagate and parent is not None and parent is not active:
            parent.merge(active)


def record_usage(
    *,
    component: str,
    purpose: str,
    provider: str,
    model: str,
    input_tokens: int | None = 0,
    cached_input_tokens: int | None = 0,
    output_tokens: int | None = 0,
    search_units: float | None = 0.0,
    requests: int = 1,
) -> None:
    meter = _ACTIVE_METER.get()
    if meter is None:
        return
    meter.record(
        UsageEvent(
            component=component,
            purpose=purpose,
            provider=provider or "",
            model=model or "",
            input_tokens=_as_int(input_tokens),
            cached_input_tokens=_as_int(cached_input_tokens),
            output_tokens=_as_int(output_tokens),
            search_units=_as_float(search_units),
            requests=requests,
        )
    )


def record_chat_usage(usage: Any, *, provider: str, model: str, purpose: str) -> None:
    """Registra el ``usage`` de una respuesta de chat completions (OpenAI SDK)."""
    if _ACTIVE_METER.get() is None:
        return
    details = _get(usage, "prompt_tokens_details")
    record_usage(
        component="chat",
        purpose=purpose,
        provider=provider,
        model=model,
        input_tokens=_get(usage, "prompt_tokens"),
        cached_input_tokens=_get(details, "cached_tokens"),
        output_tokens=_get(usage, "completion_tokens"),
    )


def record_embedding_usage(usage: Any, *, provider: str, model: str, purpose: str = "embedding") -> None:
    """Registra el ``usage`` de una respuesta de embeddings (OpenAI SDK)."""
    if _ACTIVE_METER.get() is None:
        return
    tokens = _get(usage, "prompt_tokens")
    if tokens is None:
        tokens = _get(usage, "total_tokens")
    record_usage(
        component="embeddings",
        purpose=purpose,
        provider=provider,
        model=model,
        input_tokens=tokens,
    )


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
