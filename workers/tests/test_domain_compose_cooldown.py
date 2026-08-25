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
    """Regression: a domain in its multi-day cooldown must NOT be composed into the review queue. The cooldown check has to run BEFORE the review branch, which previously composed + continued past it (so re-coverage slipped through e.g. explorer.perawallet.app a couple days after a perawallet.app article). Exercised against drain_to_compose (2026-08-25 successor to drain_standard_publish_queue) — _domain_in_cooldown itself is unchanged, reused as-is against an artifact-backed row."""
    from datetime import UTC, datetime

    from app.modules.newspaper.artifact_store import SELECTED, Artifact, ArtifactContent
    from app.modules.newspaper.tasks import queue_drain_tasks as q

    artifact = Artifact(
        artifact_id="a1",
        service_id="explorer-perawallet-app",
        url="https://explorer.perawallet.app/x",
        channel="crawler",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        event_date=None,
        priority=5.0,
        priority_computed_at=None,
        status=SELECTED,
    )
    content = ArtifactContent(
        artifact_id="a1", title="t", content="x", metadata={"payload": {"publish_kind": "discovery"}}
    )
    review_called = {"hit": False}

    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 5)
    monkeypatch.setattr(q, "_release_due_backlog", lambda _slots: None)
    monkeypatch.setattr(q, "_pending_feed_backlog_full", lambda: False)
    monkeypatch.setattr(q, "_ensure_today_selected", lambda _day: None)
    monkeypatch.setattr(
        q,
        "list_to_compose_for_day",
        lambda _day: [
            {"slot": 0, "artifact_id": "a1", "lane": "platform", "service_id": artifact.service_id}
        ],
    )
    monkeypatch.setattr(q, "get_artifact", lambda _aid: artifact)
    monkeypatch.setattr(q, "get_artifact_content", lambda _aid: content)
    monkeypatch.setattr(q, "_domain_capped", lambda _r: False)
    monkeypatch.setattr(q, "_domain_in_cooldown", lambda _r: True)  # in cooldown
    monkeypatch.setattr(q, "_row_needs_review", lambda _r: True)  # would go to review
    monkeypatch.setattr(q, "mark_artifact_status", lambda *_a, **_k: None)
    monkeypatch.setattr(q, "_resolve_dual_written_queue_row", lambda *_a, **_k: None)

    def _spy_review(*_a: object, **_k: object) -> dict:
        review_called["hit"] = True
        return {"status": "review"}

    monkeypatch.setattr(q, "publish_from_queued_row", _spy_review)
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_to_compose()
    statuses = [r.get("status") for r in out["results"]]
    assert review_called["hit"] is False, "cooldown row must not be composed for review"
    assert "domain_cooldown" in statuses
    assert "review" not in statuses
