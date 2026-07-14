from __future__ import annotations

from app.modules.newspaper import publish_schedule


class _BoomRedis:
    def get(self, key):
        raise ConnectionError("redis down")

    def set(self, key, value):
        raise ConnectionError("redis down")


def test_is_standard_publish_due_fails_closed_on_redis_error(monkeypatch) -> None:
    """A Redis outage must never look like 'clock elapsed, publish now' — that
    would silently bypass the pacing cadence (matches the backend's
    AdminCassandraStore._is_standard_publish_due, which fails the same way)."""
    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _BoomRedis())

    due, reason = publish_schedule.is_standard_publish_due()
    assert due is False
    assert reason == "redis_error"


def test_record_standard_publish_swallows_redis_error(monkeypatch) -> None:
    """The article is already committed to the feed by the time this is
    called — a Redis error here must not raise past the caller."""
    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _BoomRedis())

    publish_schedule.record_standard_publish()  # must not raise


def test_is_standard_publish_due_true_when_never_published(monkeypatch) -> None:
    class _EmptyRedis:
        def get(self, key):
            return None

    monkeypatch.setattr(publish_schedule, "_redis_client", lambda: _EmptyRedis())
    due, reason = publish_schedule.is_standard_publish_due()
    assert due is True
    assert reason == "no_prior_standard_publish"
