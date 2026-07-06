"""Per-service diversity cooldown: composing an article for a service stamps a
cooldown so the same project isn't published again from ANY of its domains
until it expires — catches the case a per-domain-only cooldown structurally
can't (e.g. a project's own site + a separate Medium blog, or the historical
Valar split across stake.valar.solutions / valar.solutions service_ids)."""

from app.modules.crawler import domain_tracker


def test_compose_stamps_cooldown(patch_redis_from_url):
    assert domain_tracker.service_in_cooldown("valar-solutions") is False
    domain_tracker.record_service_compose("valar-solutions")
    assert domain_tracker.service_in_cooldown("valar-solutions") is True
    # A different service is unaffected.
    assert domain_tracker.service_in_cooldown("tinyman") is False


def test_blank_service_is_safe(patch_redis_from_url):
    assert domain_tracker.service_in_cooldown("") is False
    domain_tracker.record_service_compose("")  # no-op
    assert patch_redis_from_url.store == {}


def test_cooldown_disabled_when_hours_zero(monkeypatch, patch_redis_from_url):
    monkeypatch.setattr(
        "app.core.config.COMPOSE_SERVICE_COOLDOWN_HOURS", 0, raising=False
    )
    domain_tracker.record_service_compose("valar-solutions")
    assert domain_tracker.service_in_cooldown("valar-solutions") is False


def test_drain_skips_row_when_service_in_cooldown(monkeypatch):
    """A service already in its cooldown must not be composed at all this run —
    even when its registrable domain differs from whatever domain last composed
    for the same service_id (multi-domain project case)."""
    from app.modules.newspaper.publish_queue_store import QueuedPublishRow
    from app.modules.newspaper.tasks import queue_drain_tasks as q

    row = QueuedPublishRow(
        queue_id="q1",
        priority=5,
        topic="",
        publish_kind="content_update",
        service_id="valar-solutions",
        display_name="",
        scrape_url="https://valar.solutions/",
        payload={},
        created_at_epoch=0,
    )

    monkeypatch.setattr(q, "remaining_standard_publish_slots", lambda: 5)
    monkeypatch.setattr(q, "_pending_for_tier", lambda *a, **k: [row])
    monkeypatch.setattr(q, "_domain_capped", lambda r: False)
    monkeypatch.setattr(q, "_domain_in_cooldown", lambda r: False)
    monkeypatch.setattr(q, "_service_in_cooldown", lambda r: True)  # in cooldown
    monkeypatch.setattr(q, "_row_needs_review", lambda r: True)  # would go to review

    def _spy_review(r):
        raise AssertionError("cooldown row must not be composed")

    monkeypatch.setattr(q, "_compose_review_row", _spy_review)
    import app.modules.crawler.classifier_review_store as crs

    monkeypatch.setattr(crs, "review_queue_full", lambda: False)

    out = q.drain_standard_publish_queue()
    statuses = [r.get("status") for r in out["results"]]
    assert "service_cooldown" in statuses
