"""rate_limit.py: per-IP fail-open, per-target fail-CLOSED (it's the DDoS defense), target_key_for."""

from __future__ import annotations

from typing import Never

import pytest

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402_uptime.services import rate_limit as uptime_rate_limit


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    def incr(self, key: str) -> int:
        value = self.store.get(key, 0) + 1
        self.store[key] = value
        return value

    def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        return True


class _BrokenRedis:
    def incr(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis down")


def _request(ip: str) -> Request:
    return Request(
        method="POST",
        headers={"x-real-ip": ip},
        query_params=QueryParams({}),
        path_params={},
        body=b"",
        url=None,
    )


def test_target_key_for_uses_host_and_port() -> None:
    """The target key is the URL's netloc -- host, plus port when non-default."""
    assert uptime_rate_limit.target_key_for("https://example.com/path") == "example.com"
    assert uptime_rate_limit.target_key_for("https://example.com:8443/path") == "example.com:8443"


def test_ip_rate_limited_trips_over_budget_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Over budget for one IP trips the limiter; a different IP is unaffected; a Redis outage fails open."""
    monkeypatch.setattr(settings, "x402_uptime_rate_limit_per_hour", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: fake)

    assert uptime_rate_limit.ip_rate_limited(_request("1.2.3.4")) is False
    assert uptime_rate_limit.ip_rate_limited(_request("1.2.3.4")) is False
    assert uptime_rate_limit.ip_rate_limited(_request("1.2.3.4")) is True
    # A different IP has its own budget.
    assert uptime_rate_limit.ip_rate_limited(_request("5.6.7.8")) is False

    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: _BrokenRedis())
    assert uptime_rate_limit.ip_rate_limited(_request("1.2.3.4")) is False  # fails open


def test_ip_rate_limited_never_limits_an_unattributable_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No X-Real-IP/X-Forwarded-For at all (e.g. local dev) is never rate-limited."""
    monkeypatch.setattr(settings, "x402_uptime_rate_limit_per_hour", 0)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: _FakeRedis())
    request = Request(
        method="POST", headers={}, query_params=QueryParams({}), path_params={}, body=b"", url=None
    )
    assert uptime_rate_limit.ip_rate_limited(request) is False


def test_target_over_budget_trips_and_is_independent_per_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting one target's real-fetch budget never affects a different target's budget."""
    monkeypatch.setattr(settings, "x402_uptime_target_rate_limit_per_hour", 1)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: fake)

    assert uptime_rate_limit.target_over_budget("victim.example") is False
    assert uptime_rate_limit.target_over_budget("victim.example") is True
    # A different target is unaffected -- the whole point of per-target scoping.
    assert uptime_rate_limit.target_over_budget("other.example") is False


def test_target_over_budget_fails_closed_on_a_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the per-IP limiter, this one IS the DDoS defense -- a Redis outage must not remove it."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: _BrokenRedis())
    assert uptime_rate_limit.target_over_budget("victim.example") is True
