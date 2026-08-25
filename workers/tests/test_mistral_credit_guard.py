"""Circuit breaker for Mistral credit exhaustion (2026-07-24): a dead API key (401/402) used to make drain_standard_publish_queue re-walk and re-hit the entire compose queue every beat (observed: hourly, 17+ hours straight) since mistral_credit_insufficient is deliberately non-terminal for transient failures — but a wiped monthly credit balance isn't transient. These tests pin the flag's fail-open behavior and TTL math; the wiring into MistralProvider._post/_fetch_model_metadata is covered by test_mistral_provider.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

import pytest

from app.modules.ai.mistral_credit_guard import (
    _seconds_until_next_month_utc,
    is_credit_exhausted,
    mark_credit_exhausted,
)


class _FakeRedis:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        self._store[f"{key}:ex"] = ex

    def get(self, key: str) -> str | None:
        return self._store.get(key)


def test_seconds_until_next_month_mid_month() -> None:
    """Computes the exact seconds remaining to the start of next month from mid-month."""
    now = datetime(2026, 7, 24, 8, 0, 0, tzinfo=UTC)
    seconds = _seconds_until_next_month_utc(now)
    expected = (datetime(2026, 8, 1, tzinfo=UTC) - now).total_seconds()
    assert seconds == int(expected)


def test_seconds_until_next_month_december_rolls_year() -> None:
    """December correctly rolls over to January of the following year."""
    now = datetime(2026, 12, 15, 12, 0, 0, tzinfo=UTC)
    seconds = _seconds_until_next_month_utc(now)
    expected = (datetime(2027, 1, 1, tzinfo=UTC) - now).total_seconds()
    assert seconds == int(expected)


def test_seconds_until_next_month_never_zero_or_negative() -> None:
    """Returns a positive TTL even when called exactly at the month-reset instant."""
    # Exactly at the reset instant — must still return a positive TTL, not 0
    # (a 0/negative `ex` would make Redis reject or immediately expire the SET).
    now = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
    assert _seconds_until_next_month_utc(now) > 0


def test_mark_then_is_exhausted_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Marking credit exhausted flips is_credit_exhausted to True with a positive TTL, not indefinite."""
    store: dict[str, str] = {}
    monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: _FakeRedis(store))

    assert is_credit_exhausted() is False
    mark_credit_exhausted()
    assert is_credit_exhausted() is True
    # TTL was set to roughly "until next month", not indefinite/omitted.
    assert store["mistral:credit_exhausted:ex"] is not None
    assert store["mistral:credit_exhausted:ex"] > 0


def test_is_exhausted_fails_open_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis error makes is_credit_exhausted fail open (return False), never blocking composition."""

    def _boom(*_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("redis.from_url", _boom)
    assert is_credit_exhausted() is False


def test_mark_does_not_raise_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """mark_credit_exhausted swallows a Redis error instead of propagating it."""

    def _boom(*_a: object, **_kw: object) -> Never:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("redis.from_url", _boom)
    mark_credit_exhausted()  # must not raise
