"""Per-domain diversity cooldown: composing an article for a registrable domain stamps a cooldown so the same project isn't published again until it expires."""

import pytest
from conftest import FakeRedis

from app.modules.crawler import domain_tracker


def test_compose_stamps_cooldown(patch_redis_from_url: FakeRedis) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Composing for a domain stamps its cooldown; a different domain is unaffected."""
    assert domain_tracker.domain_in_cooldown("perawallet.app") is False
    domain_tracker.record_domain_compose("perawallet.app")
    assert domain_tracker.domain_in_cooldown("perawallet.app") is True
    # A different project is unaffected.
    assert domain_tracker.domain_in_cooldown("tinyman.org") is False


def test_blank_domain_is_safe(patch_redis_from_url: FakeRedis) -> None:
    """An empty domain is a safe no-op that touches no Redis keys."""
    assert domain_tracker.domain_in_cooldown("") is False
    domain_tracker.record_domain_compose("")  # no-op
    assert patch_redis_from_url.store == {}


def test_cooldown_disabled_when_hours_zero(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """The cooldown never engages when its configured duration is zero hours."""
    monkeypatch.setattr("app.core.config.COMPOSE_DOMAIN_COOLDOWN_HOURS", 0, raising=False)
    domain_tracker.record_domain_compose("perawallet.app")
    assert domain_tracker.domain_in_cooldown("perawallet.app") is False


def test_drain_skips_review_bound_row_in_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a domain in its multi-day cooldown must NOT be composed into the review queue. The cooldown check has to run BEFORE the review branch, which previously composed + continued past it (so re-coverage slipped through e.g. explorer.perawallet.app a couple days after a perawallet.app article)."""
    from app.modules.newspaper.publish_queue_store import QueuedPublishRow
    from app.modules.newspaper.tasks import queue_drain_tasks as q

    row = QueuedPublishRow(
        queue_id="q1",
        priority=5,
        topic="",
        publish_kind="discovery",
        service_id="explorer-perawallet-app",
        display_name="",
        scrape_url="https://explorer.perawallet.app/x",
        payload={},
        created_at_epoch=0,
    )
    review_called = {"hit": False}

    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 5)
    monkeypatch.setattr(q, "_pending_for_tier", lambda *_a, **_k: [row])
    monkeypatch.setattr(q, "_domain_capped", lambda _r: False)
    monkeypatch.setattr(q, "_domain_in_cooldown", lambda _r: True)  # in cooldown
    monkeypatch.setattr(q, "_row_needs_review", lambda _r: True)  # would go to review
    monkeypatch.setattr(q, "mark_queue_status", lambda *_a, **_k: None)

    def _spy_review(_r: QueuedPublishRow) -> dict:
        review_called["hit"] = True
        return {"status": "review"}

    monkeypatch.setattr(q, "_compose_review_row", _spy_review)
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_standard_publish_queue()
    statuses = [r.get("status") for r in out["results"]]
    assert review_called["hit"] is False, "cooldown row must not be composed for review"
    assert "domain_cooldown" in statuses
    assert "review" not in statuses
