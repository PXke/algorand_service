"""Circuit breaker for Mistral credit exhaustion (2026-07-24): a dead API key (401/402) used to make drain_standard_publish_queue re-walk and re-hit the entire compose queue every beat (observed: hourly, 17+ hours straight) since mistral_credit_insufficient is deliberately non-terminal for transient failures — but a wiped monthly credit balance isn't transient. These tests pin the flag's fail-open behavior and TTL math; the wiring into MistralProvider._post/_fetch_model_metadata is covered by test_mistral_provider.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Never

import pytest

from app.modules.ai.mistral_credit_guard import (
    _DEEPSEEK_BREAKER_TTL_SECONDS,
    _seconds_until_next_month_utc,
    _ttl_seconds,
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


def test_ttl_seconds_deepseek_is_flat_one_hour() -> None:
    """DeepSeek's breaker TTL is a flat hour, not Mistral's until-next-month-reset math -- DeepSeek is pay-as-you-go, so a same-day top-up shouldn't stay short-circuited for weeks."""
    assert _ttl_seconds("deepseek") == _DEEPSEEK_BREAKER_TTL_SECONDS == 3600


def test_ttl_seconds_mistral_unchanged() -> None:
    """Mistral (and any other provider) keeps the original until-next-month-reset TTL, unchanged by the DeepSeek-specific carve-out."""
    now = datetime(2026, 7, 24, 8, 0, 0, tzinfo=UTC)
    assert _ttl_seconds("mistral", now) == _seconds_until_next_month_utc(now)


def test_mark_deepseek_uses_one_hour_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real (402) DeepSeek exhaustion trips the breaker with the 1h TTL, not the Mistral monthly one."""
    store: dict[str, str] = {}
    monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: _FakeRedis(store))

    mark_credit_exhausted("deepseek", status_code=402)
    assert is_credit_exhausted("deepseek") is True
    assert store["deepseek:credit_exhausted:ex"] == _DEEPSEEK_BREAKER_TTL_SECONDS


def test_mark_deepseek_401_does_not_trip_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DeepSeek 401 (auth problem) is NOT treated as credit exhaustion and must not trip the breaker -- unlike Mistral, whose real credit exhaustion showed up as a 401 historically."""
    store: dict[str, str] = {}
    monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: _FakeRedis(store))

    mark_credit_exhausted("deepseek", status_code=401)
    assert is_credit_exhausted("deepseek") is False
    assert store == {}


def test_mark_mistral_401_still_trips_breaker_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mistral keeps tripping on a 401 -- its own history is the opposite of DeepSeek's (real credit exhaustion showed up AS a 401), so the new status_code carve-out must not touch Mistral's behavior."""
    store: dict[str, str] = {}
    monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: _FakeRedis(store))

    mark_credit_exhausted("mistral", status_code=401)
    assert is_credit_exhausted("mistral") is True


def test_mark_no_status_code_still_trips_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing call sites that don't pass status_code (the default) keep tripping the breaker exactly as before the DeepSeek carve-out was added."""
    store: dict[str, str] = {}
    monkeypatch.setattr("redis.from_url", lambda *_a, **_kw: _FakeRedis(store))

    mark_credit_exhausted("deepseek")
    assert is_credit_exhausted("deepseek") is True
