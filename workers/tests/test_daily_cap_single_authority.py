"""Redundancy pruning (2026-07-18, deferred from the gate consolidation): the daily cap had two parallel counting implementations — the advisory reads (publish_policy, Cassandra feed count) and the atomic guard (Redis reservations) — which drift intra-day.

One counting authority now: the guard's reservation-aware counter; and
backlog releases reserve their slot in it like any other standard publish.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.newspaper.publish_policy import (
    PublishTier,
    remaining_breaking_publish_slots,
    remaining_standard_publish_slots,
)


def test_advisory_reads_delegate_to_guard_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advisory remaining-slot reads delegate their count to the guard's reservation-aware counter."""
    seen: list[PublishTier] = []

    def _fake_count(*, tier: PublishTier, when: datetime | None = None) -> int:  # noqa: ARG001 -- name must match the real callee's keyword arg
        seen.append(tier)
        return 2

    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.published_count_today", _fake_count
    )
    monkeypatch.setattr("app.modules.newspaper.publish_policy.config.NEWS_MAX_ARTICLES_PER_DAY", 3)
    monkeypatch.setattr("app.modules.newspaper.publish_policy.config.NEWS_MAX_BREAKING_PER_DAY", 2)
    assert remaining_standard_publish_slots() == 1
    assert remaining_breaking_publish_slots() == 0
    assert seen == [PublishTier.STANDARD, PublishTier.BREAKING]


def test_backlog_release_blocked_when_reserve_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cap-full reserve stops the release loop; the pending row survives for a later beat (no DELETE) and nothing hits the feed."""
    from app.modules.newspaper.tasks import queue_drain_tasks as qdt

    pending_row = SimpleNamespace(
        article_id="00000000-0000-0000-0000-000000000001",
        bucket="main",
        interest_score=50,
        approved_at=None,
    )
    feed_art = SimpleNamespace(
        article_id=pending_row.article_id,
        service_id="svc",
        title="T",
        summary="S",
        tags=[],
        image_url=None,
        source_url="",
        published_at=None,
    )
    deleted: list = []

    backlog_row = SimpleNamespace(
        article_id=pending_row.article_id,
        service_id="svc",
        title="T",
        interest_score=pending_row.interest_score,
        approved_at=pending_row.approved_at,
    )

    class _FakeSession:
        def execute(self, stmt: str, params: tuple | None = None) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
            text = str(stmt)
            if "status = 'backlog'" in text:
                return [backlog_row]
            if "pending_feed_queue" in text and "SELECT" in text.upper():
                return [pending_row]
            if "pending_feed_queue" in text and "DELETE" in text.upper():
                deleted.append(params)
                return SimpleNamespace(one=lambda: None)
            if "articles_by_id" in text and "SELECT" in text.upper():
                return SimpleNamespace(one=lambda: feed_art)
            if "articles_feed" in text:
                raise AssertionError("must not insert a feed row past a failed reserve")
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(
        "app.modules.newspaper.release_gates.apply_release_gates",
        lambda _aid: {"changed": False, "notes": {}},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.reserve_publish_slot",
        lambda **_kw: (False, "standard_daily_cap_hard_limit (3)"),
    )
    monkeypatch.setattr(qdt, "record_standard_publish", lambda **_kw: None)

    result = qdt._release_pending_feed_backlog(slots=1)
    assert result["published"] == 0
    assert deleted == []  # the queued row survives for a later beat


def test_missing_article_drops_queue_row_but_logs_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A pending_feed_queue row whose article record is gone (or never written) must not be silently discarded — full compose spend produced that row, so losing it without a trace is the same shape of waste as the fresh-auto-approve gate bug (2026-07-23). The row is still deleted (a permanently missing article would otherwise jam this one-row-per-run queue forever), but a warning must be logged."""
    import logging

    from app.modules.newspaper.tasks import queue_drain_tasks as qdt

    pending_row = SimpleNamespace(
        article_id="00000000-0000-0000-0000-000000000002",
        bucket="main",
        interest_score=50,
        approved_at=None,
    )
    deleted: list = []
    backlog_row = SimpleNamespace(
        article_id=pending_row.article_id,
        service_id="",
        title="",
        interest_score=pending_row.interest_score,
        approved_at=pending_row.approved_at,
    )

    class _FakeSession:
        def execute(self, stmt: str, params: tuple | None = None) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
            text = str(stmt)
            if "status = 'backlog'" in text:
                return [backlog_row]
            if "pending_feed_queue" in text and "SELECT" in text.upper():
                return [pending_row]
            if "pending_feed_queue" in text and "DELETE" in text.upper():
                deleted.append(params)
                return SimpleNamespace(one=lambda: None)
            if "articles_by_id" in text and "SELECT" in text.upper():
                return SimpleNamespace(one=lambda: None)  # article missing
            if "articles_feed" in text:
                raise AssertionError("must not insert a feed row for a missing article")
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)

    with caplog.at_level(logging.WARNING):
        result = qdt._release_pending_feed_backlog(slots=1)

    assert result["published"] == 0
    assert deleted == [
        (
            pending_row.bucket,
            pending_row.interest_score,
            pending_row.approved_at,
            pending_row.article_id,
        )
    ]
    assert any("no matching article" in rec.message for rec in caplog.records)
