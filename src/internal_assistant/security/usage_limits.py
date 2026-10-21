"""Limites de uso de la demo publica para acotar el gasto en modelos.

- Por cliente (IP; el user_id lo genera el navegador y se puede cambiar): rafaga y diario.
- Globales: peticiones al dia y al mes, y tokens al dia. Son el tope de gasto real:
  con ~0,01 $ por pregunta (LLM + reranker), 1.500 preguntas al mes son ~15 $ en el peor caso.

Se cuenta en Postgres para que valga con varios workers y tras reinicios.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from internal_assistant.models import UsageEvent


@dataclass(frozen=True, slots=True)
class LimitDecision:
    allowed: bool
    reason: str = ""
    message: str = ""
    retry_after_seconds: int = 0


def client_key(ip: str | None) -> str:
    """Identificador estable y anonimo del cliente (no se guarda la IP en claro)."""
    return hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()[:32]


def client_ip(headers, fallback: str | None) -> str:
    """IP real detras del front de App Service (X-Forwarded-For, con o sin puerto)."""
    forwarded = (headers.get("x-forwarded-for") or headers.get("x-client-ip") or "").split(",")[0].strip()
    candidate = forwarded or (fallback or "")
    if candidate.count(":") == 1:  # IPv4 con puerto
        candidate = candidate.split(":")[0]
    return candidate or "unknown"


class UsageLimiter:
    def __init__(self, session: Session, settings) -> None:
        self.session = session
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "usage_limits_enabled", True))

    def _count(self, since: datetime, key: str | None = None) -> int:
        statement = select(func.count(UsageEvent.id)).where(UsageEvent.created_at >= since)
        if key is not None:
            statement = statement.where(UsageEvent.client_key == key)
        return int(self.session.execute(statement).scalar() or 0)

    def _tokens(self, since: datetime) -> int:
        statement = select(func.coalesce(func.sum(UsageEvent.tokens), 0)).where(UsageEvent.created_at >= since)
        return int(self.session.execute(statement).scalar() or 0)

    def check(self, key: str, *, now: datetime | None = None) -> LimitDecision:
        if not self.enabled:
            return LimitDecision(allowed=True)
        now = now or datetime.now(timezone.utc)
        s = self.settings
        burst_window = int(s.usage_client_burst_window_seconds)
        if self._count(now - timedelta(seconds=burst_window), key) >= int(s.usage_client_burst_requests):
            return LimitDecision(
                False,
                "client_burst",
                f"Has hecho muchas preguntas seguidas. Espera {max(1, burst_window // 60)} minutos y vuelve a intentarlo.",
                burst_window,
            )
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_to_midnight = int((start_of_day + timedelta(days=1) - now).total_seconds())
        if self._count(start_of_day, key) >= int(s.usage_client_daily_requests):
            return LimitDecision(
                False, "client_daily", "Has llegado al limite diario de preguntas de esta demo. Vuelve manana.", seconds_to_midnight
            )
        if self._count(start_of_day) >= int(s.usage_global_daily_requests) or self._tokens(start_of_day) >= int(
            s.usage_global_daily_tokens
        ):
            return LimitDecision(
                False,
                "global_daily",
                "La demo ha alcanzado su limite de uso de hoy. Vuelve manana.",
                seconds_to_midnight,
            )
        start_of_month = start_of_day.replace(day=1)
        if self._count(start_of_month) >= int(s.usage_global_monthly_requests):
            return LimitDecision(
                False, "global_monthly", "La demo ha alcanzado su limite de uso de este mes.", seconds_to_midnight
            )
        return LimitDecision(allowed=True)

    def record(self, key: str, tokens: int) -> None:
        self.session.add(UsageEvent(client_key=key, tokens=max(0, int(tokens or 0))))
        self.session.commit()


def total_tokens(meter) -> int:
    if meter is None:
        return 0
    return sum(event.input_tokens + event.output_tokens for event in meter.events)
