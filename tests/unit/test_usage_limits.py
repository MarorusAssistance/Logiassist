from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from internal_assistant.models import UsageEvent
from internal_assistant.security.usage_limits import UsageLimiter, client_ip, client_key

NOW = datetime(2026, 10, 21, 12, 0, 0)


def _settings(**overrides):
    values = dict(
        usage_limits_enabled=True,
        usage_client_burst_requests=3,
        usage_client_burst_window_seconds=300,
        usage_client_daily_requests=5,
        usage_global_daily_requests=8,
        usage_global_daily_tokens=10_000,
        usage_global_monthly_requests=20,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _session():
    engine = create_engine("sqlite://")
    UsageEvent.__table__.create(engine)
    return Session(engine)


def _add(session, key, *, minutes_ago=0, days_ago=0, tokens=100):
    session.add(UsageEvent(client_key=key, tokens=tokens, created_at=NOW - timedelta(minutes=minutes_ago, days=days_ago)))
    session.commit()


def test_client_burst_and_daily_limits():
    session = _session()
    limiter = UsageLimiter(session, _settings())
    for minute in (1, 2, 3):
        _add(session, "a", minutes_ago=minute)
    assert limiter.check("a", now=NOW).reason == "client_burst"
    assert limiter.check("b", now=NOW).allowed

    for minute in (60, 61):
        _add(session, "c", minutes_ago=minute)
    _add(session, "c", minutes_ago=62)
    _add(session, "c", minutes_ago=63)
    _add(session, "c", minutes_ago=64)
    decision = limiter.check("c", now=NOW)
    assert not decision.allowed and decision.reason == "client_daily" and decision.retry_after_seconds > 0


def test_global_daily_requests_tokens_and_monthly_limits():
    session = _session()
    for index in range(8):
        _add(session, f"k{index}", minutes_ago=30 + index)
    assert UsageLimiter(session, _settings()).check("new", now=NOW).reason == "global_daily"

    session = _session()
    _add(session, "x", minutes_ago=30, tokens=10_000)
    assert UsageLimiter(session, _settings()).check("new", now=NOW).reason == "global_daily"

    session = _session()
    for index in range(20):
        _add(session, f"m{index}", days_ago=3 + (index % 10))
    assert UsageLimiter(session, _settings()).check("new", now=NOW).reason == "global_monthly"


def test_disabled_limits_always_allow():
    session = _session()
    for minute in range(10):
        _add(session, "a", minutes_ago=minute)
    assert UsageLimiter(session, _settings(usage_limits_enabled=False)).check("a", now=NOW).allowed


def test_client_ip_uses_forwarded_header_without_port():
    assert client_ip({"x-forwarded-for": "81.2.3.4:51234, 10.0.0.1"}, "10.0.0.9") == "81.2.3.4"
    assert client_ip({}, "127.0.0.1") == "127.0.0.1"
    assert client_ip({"x-forwarded-for": "2001:db8::1"}, None) == "2001:db8::1"
    assert len(client_key("81.2.3.4")) == 32 and client_key("81.2.3.4") != "81.2.3.4"
