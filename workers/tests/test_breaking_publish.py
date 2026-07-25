"""Breaking-tier classification and credibility gating for scam/incident drafts."""

import pytest

from app.modules.newspaper.breaking_credibility import assess_breaking_credibility
from app.modules.newspaper.publish_policy import (
    PublishTier,
    PublishTopic,
    build_publish_intent,
)
from app.modules.newspaper.publish_schedule import is_standard_publish_due


def test_scam_classified_as_breaking_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scam-alert page is classified into the breaking tier when BREAKING_TIER_ENABLED is on."""
    # BREAKING_TIER_ENABLED defaults off (2026-07-17) — pins the underlying
    # keyword logic for when it's re-enabled.
    monkeypatch.setattr("app.core.config.BREAKING_TIER_ENABLED", True)
    intent = build_publish_intent(
        service_id="discord-alerts",
        page_text="SCAM ALERT: user lost $100,000 — see https://example.com/proof",
        is_first_snapshot=False,
        diff="+ warning line",
    )
    assert intent.topic == PublishTopic.SCAM_ALERT
    assert intent.tier == PublishTier.BREAKING


def test_scam_stays_standard_tier_while_breaking_disabled() -> None:
    """A scam-alert page still classifies as scam_alert topic but stays STANDARD tier by default."""
    intent = build_publish_intent(
        service_id="discord-alerts",
        page_text="SCAM ALERT: user lost $100,000 — see https://example.com/proof",
        is_first_snapshot=False,
        diff="+ warning line",
    )
    assert intent.topic == PublishTopic.SCAM_ALERT
    assert intent.tier == PublishTier.STANDARD


def test_sdk_stays_standard_tier() -> None:
    """An SDK release note classifies as SDK_RELEASE topic in the STANDARD tier."""
    intent = build_publish_intent(
        service_id="dev-tools",
        page_text="SDK v2.0 release notes on GitHub",
        is_first_snapshot=False,
        diff="+ changelog",
    )
    assert intent.topic == PublishTopic.SDK_RELEASE
    assert intent.tier == PublishTier.STANDARD


def test_breaking_heuristic_credibility() -> None:
    """A scam alert with a victim amount and a linked source URL passes the heuristic as credible."""
    assessment = assess_breaking_credibility(
        page_text="Scam alert: victim lost $100,000 https://gov.example/notice",
        source_url="discord://channel/1",
        topic="scam_alert",
    )
    assert assessment.credible
    assert assessment.method == "heuristic"


def test_standard_interval_not_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """A standard publish isn't due yet when the pacing interval hasn't elapsed."""
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.last_standard_publish_epoch",
        lambda: 1_000_000_000,
    )
    due, reason = is_standard_publish_due(now_epoch=1_000_000_100)
    assert not due
    assert "wait_standard_interval" in reason
