from __future__ import annotations

from app.modules.newspaper.tasks import queue_drain_tasks as q


def test_drain_standard_releases_pending_feed_backlog_before_composing(monkeypatch) -> None:
    """drain_approved_feed_queue's pending_feed_queue release was folded into
    drain_standard_publish_queue as an early step (2026-07-14) — when a
    backlog item is due for release, it must go out WITHOUT also attempting
    to compose a new item from publish_queue in the same run."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(
        q, "_release_pending_feed_backlog", lambda *, slots: {"status": "ok", "published": 1}
    )

    def _spy_pending_for_tier(*a, **kw):
        raise AssertionError("must not consider composing when backlog released something")

    monkeypatch.setattr(q, "_pending_for_tier", _spy_pending_for_tier)

    out = q.drain_standard_publish_queue()
    assert out["status"] == "ok"
    assert out["published"] == 1
    assert out["source"] == "pending_feed_backlog"


def test_drain_standard_falls_through_to_compose_when_backlog_empty(monkeypatch) -> None:
    """No backlog item released (pending_feed_queue empty) — the existing
    compose-from-publish_queue path must still run unchanged."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(
        q, "_release_pending_feed_backlog", lambda *, slots: {"status": "ok", "published": 0}
    )
    monkeypatch.setattr(q, "_pending_for_tier", lambda *a, **k: [])
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_standard_publish_queue()
    assert out["status"] == "ok"
    assert out["tier"] == "standard"
    assert "source" not in out


def test_drain_standard_skips_backlog_release_when_not_due(monkeypatch) -> None:
    """The interval gate on the backlog-release step must not be bypassed —
    only the review-composition path below is intentionally interval-exempt."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (False, "wait_standard_interval (100s remaining)"),
    )

    def _spy_backlog(*, slots):
        raise AssertionError("must not attempt backlog release when not due")

    monkeypatch.setattr(q, "_release_pending_feed_backlog", _spy_backlog)
    monkeypatch.setattr(q, "_pending_for_tier", lambda *a, **k: [])
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_standard_publish_queue()
    assert out["status"] == "ok"
    assert "source" not in out
