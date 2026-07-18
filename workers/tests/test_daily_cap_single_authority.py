"""Redundancy pruning (2026-07-18, deferred from the gate consolidation):
the daily cap had two parallel counting implementations — the advisory reads
(publish_policy, Cassandra feed count) and the atomic guard (Redis
reservations) — which drift intra-day. One counting authority now: the
guard's reservation-aware counter; and backlog releases reserve their slot
in it like any other standard publish."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.newspaper.publish_policy import (
    PublishTier,
    remaining_breaking_publish_slots,
    remaining_standard_publish_slots,
)


def test_advisory_reads_delegate_to_guard_counter(monkeypatch):
    seen: list[PublishTier] = []

    def _fake_count(*, tier, when=None):
        seen.append(tier)
        return 2

    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.published_count_today", _fake_count
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.NEWS_MAX_ARTICLES_PER_DAY", 3
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_policy.config.NEWS_MAX_BREAKING_PER_DAY", 2
    )
    assert remaining_standard_publish_slots() == 1
    assert remaining_breaking_publish_slots() == 0
    assert seen == [PublishTier.STANDARD, PublishTier.BREAKING]


def test_backlog_release_blocked_when_reserve_fails(monkeypatch):
    """A cap-full reserve stops the release loop; the pending row survives
    for a later beat (no DELETE) and nothing hits the feed."""
    from app.modules.newspaper.tasks import queue_drain_tasks as qdt

    pending_row = SimpleNamespace(
        article_id="00000000-0000-0000-0000-000000000001",
        bucket="main", interest_score=50, approved_at=None,
    )
    feed_art = SimpleNamespace(
        article_id=pending_row.article_id, service_id="svc", title="T",
        summary="S", tags=[], image_url=None, source_url="", published_at=None,
    )
    deleted: list = []

    class _FakeSession:
        def execute(self, stmt, params=None):
            text = str(stmt)
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
