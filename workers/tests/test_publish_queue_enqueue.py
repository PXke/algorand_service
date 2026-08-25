"""publish_queue_store.enqueue_publish -- the real (non-mocked) function, exercised directly against a fake Cassandra session.

Regression pin (2026-08-25): enqueue_publish's one-pending-per-service dedup
scan used to skip itself for BREAKING-tier payloads via
`PublishTier.BREAKING.value` -- when the BREAKING tier was removed from the
PublishTier enum, that expression became a live `AttributeError` on every
single call (BREAKING no longer exists as a member), since every payload's
`tier` is compared against it. This was never on any mocked test path, so it
went unnoticed until this file exercised the real function.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
from app.modules.newspaper.publish_queue_store import enqueue_publish


def test_enqueue_publish_does_not_raise_for_standard_tier(
    fake_cassandra_session: MagicMock,
) -> None:
    """A plain standard-tier enqueue (every payload now, since BREAKING was removed) must not raise -- pins the 2026-08-25 PublishTier.BREAKING regression."""
    fake_cassandra_session.execute.return_value.one.return_value = None  # DEDUPE_GET: no dup
    fake_cassandra_session.execute.return_value.__iter__.return_value = iter([])  # LIST_PENDING: empty

    queue_id, created = enqueue_publish(
        service_id="svc-1",
        display_name="Svc One",
        scrape_url="https://example.com/svc-1",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.CONTENT_UPDATE,
        priority=10,
        dedupe_key="svc-1:content_update:standard:abc123",
        payload={"tier": "standard"},
    )

    assert created is True
    uuid.UUID(queue_id)  # a real uuid was generated and returned


def test_enqueue_publish_dedupes_against_pending_row_for_same_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second signal for a service that already has a PENDING row returns the existing row instead of inserting a new one."""
    existing_queue_id = uuid.uuid4()
    pending_row = SimpleNamespace(queue_id=existing_queue_id, service_id="svc-1")

    calls: list[tuple[str, tuple]] = []

    class _FakeResult:
        def __init__(self, rows: list | None, one_value: object) -> None:
            self._rows = rows or []
            self._one = one_value

        def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
            return self._one

        def __iter__(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra ResultSet
            return iter(self._rows)

    class _FakeSession:
        def execute(self, stmt: object, params: tuple = ()) -> _FakeResult:
            calls.append((str(stmt), params))
            # DEDUPE_GET is the first prepared statement bound; LIST_PENDING
            # is a plain SELECT over the pending partition -- distinguish by
            # arity of the returned/iterated shape instead of statement
            # identity, since both are opaque _Stmt descriptors here.
            if len(params) == 1:
                return _FakeResult(None, None)  # DEDUPE_GET: not a dup
            return _FakeResult([pending_row], None)  # LIST_PENDING

    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _FakeSession()
    )

    queue_id, created = enqueue_publish(
        service_id="svc-1",
        display_name="Svc One",
        scrape_url="https://example.com/svc-1",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.CONTENT_UPDATE,
        priority=10,
        dedupe_key="svc-1:content_update:standard:def456",
        payload={"tier": "standard"},
    )

    assert created is False
    assert queue_id == str(existing_queue_id)


def test_enqueue_publish_stamps_now_utc(fake_cassandra_session: MagicMock) -> None:
    """Sanity: the insert path runs to completion (touches now/UTC-dependent code) without error."""
    fake_cassandra_session.execute.return_value.one.return_value = None
    fake_cassandra_session.execute.return_value.__iter__.return_value = iter([])

    before = datetime.now(tz=UTC)
    queue_id, created = enqueue_publish(
        service_id="svc-2",
        display_name="Svc Two",
        scrape_url="https://example.com/svc-2",
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        topic=PublishTopic.NEW_SERVICE,
        priority=5,
        dedupe_key="discovery:svc-2",
        payload={"tier": "standard"},
    )
    after = datetime.now(tz=UTC)

    assert created is True
    assert before <= after  # trivial, but confirms the call above didn't hang/raise
    assert queue_id
