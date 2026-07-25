"""Standard-publish pacing fails closed on Redis errors."""

from __future__ import annotations

from typing import Never

import pytest

from app.modules.newspaper import publish_schedule


class _BoomRedis:
    def get(self, _key: str) -> Never:
        raise ConnectionError("redis down")

    def set(self, _key: str, _value: str) -> Never:
        raise ConnectionError("redis down")


def test_is_standard_publish_due_fails_closed_on_redis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis outage must never look like 'clock elapsed, publish now' — that would silently bypass the pacing cadence (matches the backend's AdminCassandraStore._is_standard_publish_due, which fails the same way)."""
    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _BoomRedis())

    due, reason = publish_schedule.is_standard_publish_due()
    assert due is False
    assert reason == "redis_error"


def test_record_standard_publish_swallows_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The article is already committed to the feed by the time this is called — a Redis error here must not raise past the caller."""
    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _BoomRedis())

    publish_schedule.record_standard_publish()  # must not raise


def test_is_standard_publish_due_true_when_never_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishing is due immediately when Redis has no record of a prior standard publish."""
    class _EmptyRedis:
        def get(self, _key: str) -> None:
            return None

    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _EmptyRedis())
    due, reason = publish_schedule.is_standard_publish_due()
    assert due is True
    assert reason == "no_prior_standard_publish"
