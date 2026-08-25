"""drain_to_compose releases the pending feed backlog before composing anything new."""

from __future__ import annotations

from typing import Never

import pytest

from app.modules.newspaper.tasks import queue_drain_tasks as q


def test_drain_releases_pending_feed_backlog_before_composing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """drain_approved_feed_queue's pending_feed_queue release was folded into drain_standard_publish_queue as an early step (2026-07-14), inherited unchanged by its 2026-08-25 successor drain_to_compose — when a backlog item is due for release, it must go out WITHOUT also attempting to compose a new item from today's to_compose selection in the same run."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(
        q,
        "_release_pending_feed_backlog",
        lambda *, slots: {"status": "ok", "published": 1},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )

    def _spy_ensure_selected(_day: str) -> Never:
        raise AssertionError("must not consider composing when backlog released something")

    monkeypatch.setattr(q, "_ensure_today_selected", _spy_ensure_selected)

    out = q.drain_to_compose()
    assert out["status"] == "ok"
    assert out["published"] == 1
    assert out["source"] == "pending_feed_backlog"


def test_drain_falls_through_to_compose_when_backlog_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No backlog item released (pending_feed_queue empty) — the existing compose-from-to_compose path must still run unchanged."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (True, "no_prior_standard_publish"),
    )
    monkeypatch.setattr(
        q,
        "_release_pending_feed_backlog",
        lambda *, slots: {"status": "ok", "published": 0},  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    monkeypatch.setattr(q, "_ensure_today_selected", lambda _day: None)
    monkeypatch.setattr(q, "list_to_compose_for_day", lambda _day: [])
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_to_compose()
    assert out["status"] == "ok"
    assert out["tier"] == "standard"
    assert "source" not in out
    assert out["reason"] == "no_selection_for_today"


def test_drain_skips_backlog_release_when_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """The interval gate on the backlog-release step must not be bypassed — only the review-composition path below is intentionally interval-exempt."""
    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.is_standard_publish_due",
        lambda: (False, "wait_standard_interval (100s remaining)"),
    )

    def _spy_backlog(*, slots: int) -> Never:  # noqa: ARG001 -- name must match the real callee's keyword arg
        raise AssertionError("must not attempt backlog release when not due")

    monkeypatch.setattr(q, "_release_pending_feed_backlog", _spy_backlog)
    monkeypatch.setattr(q, "_ensure_today_selected", lambda _day: None)
    monkeypatch.setattr(q, "list_to_compose_for_day", lambda _day: [])
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_to_compose()
    assert out["status"] == "ok"
    assert "source" not in out
