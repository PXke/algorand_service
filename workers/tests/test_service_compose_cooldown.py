"""Per-service diversity cooldown: composing an article for a service stamps a cooldown so the same project isn't published again from ANY of its domains until it expires — catches the case a per-domain-only cooldown structurally can't (e.g. a project's own site + a separate Medium blog, or the historical Valar split across stake.valar.solutions / valar.solutions service_ids)."""

from typing import Never

import pytest
from conftest import FakeRedis

from app.modules.crawler import domain_tracker


def test_compose_stamps_cooldown(patch_redis_from_url: FakeRedis) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Stamps a cooldown on compose that blocks that service (but not others) until it expires."""
    assert domain_tracker.service_in_cooldown("valar-solutions") is False
    domain_tracker.record_service_compose("valar-solutions")
    assert domain_tracker.service_in_cooldown("valar-solutions") is True
    # A different service is unaffected.
    assert domain_tracker.service_in_cooldown("tinyman") is False


def test_blank_service_is_safe(patch_redis_from_url: FakeRedis) -> None:
    """Treats a blank service_id as never in cooldown and skips writing anything to Redis."""
    assert domain_tracker.service_in_cooldown("") is False
    domain_tracker.record_service_compose("")  # no-op
    assert patch_redis_from_url.store == {}


def test_cooldown_disabled_when_hours_zero(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001 -- name must match the real callee's keyword arg
) -> None:
    """Disables the cooldown entirely when COMPOSE_SERVICE_COOLDOWN_HOURS is zero."""
    monkeypatch.setattr("app.core.config.COMPOSE_SERVICE_COOLDOWN_HOURS", 0, raising=False)
    domain_tracker.record_service_compose("valar-solutions")
    assert domain_tracker.service_in_cooldown("valar-solutions") is False


def test_drain_skips_row_when_service_in_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service already in its cooldown must not be composed at all this run — even when its registrable domain differs from whatever domain last composed for the same service_id (multi-domain project case). Exercised against drain_to_compose (2026-08-25 successor to drain_standard_publish_queue) — _service_in_cooldown itself is unchanged, reused as-is against an artifact-backed row."""
    from datetime import UTC, datetime

    from algorand_shared.artifact_store import SELECTED, Artifact, ArtifactContent

    from app.modules.newspaper.tasks import queue_drain_tasks as q

    artifact = Artifact(
        artifact_id="a1",
        service_id="valar-solutions",
        url="https://valar.solutions/",
        channel="crawler",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        event_date=None,
        priority=5.0,
        priority_computed_at=None,
        status=SELECTED,
    )
    content = ArtifactContent(
        artifact_id="a1",
        title="t",
        content="x",
        metadata={"payload": {"publish_kind": "content_update"}},
    )

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
    monkeypatch.setattr(q, "_domain_in_cooldown", lambda _r: False)
    monkeypatch.setattr(q, "_service_in_cooldown", lambda _r: True)  # in cooldown
    monkeypatch.setattr(q, "_row_needs_review", lambda _r: True)  # would go to review
    monkeypatch.setattr(q, "mark_artifact_status", lambda *_a, **_k: None)

    def _spy_review(*_a: object, **_k: object) -> Never:
        raise AssertionError("cooldown row must not be composed")

    monkeypatch.setattr(q, "publish_from_queued_row", _spy_review)
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_to_compose()
    statuses = [r.get("status") for r in out["results"]]
    assert "service_cooldown" in statuses
