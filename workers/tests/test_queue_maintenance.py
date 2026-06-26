from __future__ import annotations

from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTopic,
    classify_publish_topic,
)


def test_chain_triggered_generic_becomes_chain_activity() -> None:
    topic = classify_publish_topic(
        page_text="A regular page without notable phrases.",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=True,
    )
    assert topic == PublishTopic.CHAIN_ACTIVITY


def test_chain_triggered_does_not_override_scam() -> None:
    topic = classify_publish_topic(
        page_text="Scam alert: phishing site draining wallets",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=True,
    )
    assert topic == PublishTopic.SCAM_ALERT


def test_non_chain_content_update_unchanged() -> None:
    topic = classify_publish_topic(
        page_text="A regular page without notable phrases.",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=False,
    )
    assert topic == PublishTopic.CONTENT_UPDATE
