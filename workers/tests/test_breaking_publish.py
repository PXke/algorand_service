from app.modules.newspaper.breaking_credibility import assess_breaking_credibility
from app.modules.newspaper.publish_policy import (
    PublishTier,
    PublishTopic,
    build_publish_intent,
    classify_publish_tier,
)
from app.modules.newspaper.publish_schedule import is_standard_publish_due


def test_scam_classified_as_breaking_tier() -> None:
    intent = build_publish_intent(
        service_id="discord-alerts",
        page_text="SCAM ALERT: user lost $100,000 — see https://example.com/proof",
        is_first_snapshot=False,
        diff="+ warning line",
    )
    assert intent.topic == PublishTopic.SCAM_ALERT
    assert intent.tier == PublishTier.BREAKING


def test_sdk_stays_standard_tier() -> None:
    intent = build_publish_intent(
        service_id="dev-tools",
        page_text="SDK v2.0 release notes on GitHub",
        is_first_snapshot=False,
        diff="+ changelog",
    )
    assert intent.topic == PublishTopic.SDK_RELEASE
    assert intent.tier == PublishTier.STANDARD


def test_breaking_heuristic_credibility() -> None:
    assessment = assess_breaking_credibility(
        page_text="Scam alert: victim lost $100,000 https://gov.example/notice",
        source_url="discord://channel/1",
        topic="scam_alert",
    )
    assert assessment.credible
    assert assessment.method == "heuristic"


def test_standard_interval_not_due(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.publish_schedule.last_standard_publish_epoch",
        lambda: 1_000_000_000,
    )
    due, reason = is_standard_publish_due(now_epoch=1_000_000_100)
    assert not due
    assert "wait_standard_interval" in reason
