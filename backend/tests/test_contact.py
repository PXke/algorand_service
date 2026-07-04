from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.core import serialization
from app.modules.contact.api.routes import _rate_limited
from app.modules.contact.store import list_recent
from app.schemas import ContactMessageRequest


def test_contact_request_decode_defaults() -> None:
    payload = serialization.decode(
        b'{"message": "hello from a reader"}', ContactMessageRequest
    )
    assert payload.message == "hello from a reader"
    assert payload.name == "" and payload.email == "" and payload.website == ""


def test_contact_request_rejects_short_message() -> None:
    try:
        serialization.decode(b'{"message": "hi"}', ContactMessageRequest)
    except serialization.DecodeError:
        return
    raise AssertionError("short message should not decode")


def test_rate_limited_counts_per_ip(monkeypatch) -> None:
    counts: dict[str, int] = {}

    class FakeRedis:
        def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def expire(self, key: str, ttl: int) -> None:
            assert ttl == 3600

    monkeypatch.setattr("app.modules.contact.api.routes._redis", lambda: FakeRedis())
    assert all(not _rate_limited("1.2.3.4") for _ in range(5))
    assert _rate_limited("1.2.3.4")
    assert not _rate_limited("5.6.7.8")  # separate ip has its own budget


def test_rate_limited_fails_open(monkeypatch) -> None:
    def boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.modules.contact.api.routes._redis", boom)
    assert not _rate_limited("1.2.3.4")
    assert not _rate_limited("")  # no ip header → cannot key a bucket, let it pass


def test_list_recent_merges_buckets_newest_first(monkeypatch) -> None:
    def row(minute: int):
        return SimpleNamespace(
            message_id=uuid4(),
            name="n",
            email="e",
            message="m",
            created_at=datetime(2026, 7, 4, 12, minute, tzinfo=UTC),
        )

    per_bucket = {"old": [row(1)], "new": [row(3), row(2)]}
    buckets_queried: list[str] = []

    class FakeSession:
        def execute(self, stmt, params):
            bucket = params[0]
            buckets_queried.append(bucket)
            return per_bucket["new" if bucket == datetime.now(tz=UTC).strftime("%Y-%m") else "old"]

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)

    items = list_recent()
    assert len(buckets_queried) == 2  # current + previous month
    epochs = [i.created_at_epoch for i in items]
    assert epochs == sorted(epochs, reverse=True)
    assert len(items) == 3
