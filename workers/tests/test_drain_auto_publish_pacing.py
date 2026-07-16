"""Incident 2026-07-15: one drain run chain-published three articles minutes
apart (Subtopia 11:27, Aramid 11:33, Silo 11:37) instead of 8h apart.

The review branch of drain_standard_publish_queue composes rows the
classifier wasn't confident about; fresh-auto-approve inside
publish_from_queued_row can turn such a compose into a direct feed publish
(status "published"). Before the fix, that outcome advanced neither the
pacing clock (record_standard_publish) nor the run's feed budget
(`published`), and the review batch limit only counted literal "review"
outcomes — so the loop kept composing and publishing until the daily cap.

Contract pinned here:
- a "published" outcome from the review branch advances the pacing clock and
  spends the one-feed-publish-per-run budget;
- an "approved_backlog" outcome counts toward the compose batch limit;
- "approved_backlog" is a terminal queue outcome (row dequeued, article kept).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.newspaper.publish_queue_store import TERMINAL_OUTCOMES
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _row(queue_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        queue_id=queue_id,
        service_id=f"svc-{queue_id}",
        scrape_url=f"https://example.com/{queue_id}",
        publish_kind="content_update",
        topic="generic",
        payload={"page_text": "text", "signals": None},
    )


@pytest.fixture
def drain_env(monkeypatch):
    """Neutralize everything around the review-branch accounting under test."""
    monkeypatch.setattr(qdt, "_pending_feed_backlog_full", lambda: False)
    monkeypatch.setattr(qdt, "remaining_standard_publish_slots", lambda: 3)
    # Backlog-release step: not due, so it stays out of the way.
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda **_kw: (False, "wait"),
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: False,
    )
    monkeypatch.setattr(qdt, "_run_pre_compose_gates", lambda _row: None)
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _row: True)

    recorded: list[str] = []
    monkeypatch.setattr(
        qdt, "record_standard_publish", lambda **_kw: recorded.append("tick")
    )
    return recorded


def test_auto_published_review_outcome_advances_clock_and_spends_budget(
    monkeypatch, drain_env
) -> None:
    rows = [_row("r1"), _row("r2")]
    monkeypatch.setattr(qdt, "_pending_for_tier", lambda _tier, limit: rows)
    # r2 is a direct-publish row; it must never be composed once r1's
    # auto-publish spent the run's feed budget.
    monkeypatch.setattr(
        qdt,
        "_row_needs_review",
        lambda row: row.queue_id == "r1",
    )
    composed: list[str] = []

    def compose(row):
        composed.append(row.queue_id)
        return {"status": "published", "article_id": "a-1"}

    monkeypatch.setattr(qdt, "_compose_review_row", compose)
    monkeypatch.setattr(
        qdt,
        "publish_from_queued_row",
        lambda *a, **k: pytest.fail("direct publish must not run after budget spent"),
    )

    result = qdt.drain_standard_publish_queue()

    assert composed == ["r1"]
    assert drain_env == ["tick"]  # pacing clock advanced exactly once
    assert result["published"] == 1


def test_approved_backlog_outcome_counts_toward_compose_batch_limit(
    monkeypatch, drain_env
) -> None:
    rows = [_row("r1"), _row("r2")]
    monkeypatch.setattr(qdt, "_pending_for_tier", lambda _tier, limit: rows)
    monkeypatch.setattr(qdt.config, "REVIEW_COMPOSE_BATCH_LIMIT", 1, raising=False)
    composed: list[str] = []

    def compose(row):
        composed.append(row.queue_id)
        return {"status": "approved_backlog", "article_id": "a-1"}

    monkeypatch.setattr(qdt, "_compose_review_row", compose)

    qdt.drain_standard_publish_queue()

    # One compose spent the batch budget; the second row waits for a later run.
    assert composed == ["r1"]
    assert drain_env == []  # nothing hit the feed — clock untouched


def test_approved_backlog_is_terminal() -> None:
    assert "approved_backlog" in TERMINAL_OUTCOMES


def test_full_backlog_stops_review_composes(monkeypatch, drain_env) -> None:
    """2026-07-16: auto-approve → backlog bypassed the 1-slot review throttle,
    so hourly drains composed six articles overnight — two days of publish
    inventory at 3/day. With PENDING_FEED_MAX_DEPTH articles already queued,
    review-bound rows must stay pending, uncomposed."""
    monkeypatch.setattr(qdt, "_pending_feed_backlog_full", lambda: True)
    rows = [_row("r1"), _row("r2")]
    monkeypatch.setattr(qdt, "_pending_for_tier", lambda _tier, limit: rows)
    monkeypatch.setattr(
        qdt,
        "_compose_review_row",
        lambda row: pytest.fail("must not compose while the backlog is full"),
    )

    result = qdt.drain_standard_publish_queue()

    assert result["published"] == 0
    assert drain_env == []  # clock untouched — nothing happened


def test_ensure_review_ready_skips_when_backlog_full(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: False,
    )
    monkeypatch.setattr(qdt, "_pending_feed_backlog_full", lambda: True)
    monkeypatch.setattr(
        qdt,
        "_pending_for_tier",
        lambda _tier, limit: pytest.fail("must not even list candidates"),
    )
    result = qdt.ensure_review_ready()
    assert result == {"status": "skipped", "reason": "pending_feed_backlog_full"}


def test_capped_compose_is_stashed_to_backlog_not_discarded(monkeypatch) -> None:
    """2026-07-15: a finished ok compose (the 'Seven Real-World Apps' YouTube
    article) hit 'standard daily publish cap reached (3/3)' AFTER composing,
    got returned as rate_limited, and the content was thrown away (its queue
    row later aged out). The stash helper must store the article unlisted and
    queue it for the paced backlog release instead."""
    from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
    from app.modules.newspaper.tasks import publish_tasks as pt

    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True))[1],
    )
    executed: list = []

    class _FakeSession:
        def execute(self, stmt, params=None):
            executed.append((stmt, params))

    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _FakeSession()
    )
    monkeypatch.setattr(
        "app.core.cassandra.prepare_cached", lambda cql: cql
    )

    row = SimpleNamespace(
        queue_id="q1",
        service_id="youtube-algorand-foundation",
        scrape_url="https://www.youtube.com/watch?v=3hiqzTfcdF4",
        payload={"txid": "", "round_num": 0},
    )
    composed = SimpleNamespace(
        title="Seven Real-World Apps",
        summary="s",
        body="body text",
        publish_kind=None,
        extra_tags=(),
        prompt_version="2026-07-16a",
    )
    out = pt._stash_capped_compose_to_backlog(
        row=row,
        composed=composed,
        payload=row.payload,
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.GENERIC,
        tier=pt.PublishTier.STANDARD,
        reason="standard daily publish cap reached (3/3)",
    )

    assert out["status"] == "approved_backlog"
    assert stored["publish_to_feed"] is False
    assert stored["title"] == "Seven Real-World Apps"
    assert len(executed) == 1  # the pending_feed_queue INSERT
    assert "pending_feed_queue" in str(executed[0][0])
