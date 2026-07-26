"""Chain-triggered generic rows are retagged, without overriding scam framing."""

from __future__ import annotations

from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTopic,
    classify_publish_topic,
)


def test_chain_triggered_generic_becomes_chain_activity() -> None:
    """A chain-triggered generic row is retagged as chain-activity."""
    topic = classify_publish_topic(
        page_text="A regular page without notable phrases.",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=True,
    )
    assert topic == PublishTopic.CHAIN_ACTIVITY


def test_chain_triggered_does_not_override_scam() -> None:
    """A chain-triggered row already matching scam phrasing keeps its scam-alert topic."""
    topic = classify_publish_topic(
        page_text="Scam alert: phishing site draining wallets",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=True,
    )
    assert topic == PublishTopic.SCAM_ALERT


def test_non_chain_content_update_unchanged() -> None:
    """A non-chain-triggered content-update row is classified unchanged."""
    topic = classify_publish_topic(
        page_text="A regular page without notable phrases.",
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        chain_triggered=False,
    )
    assert topic == PublishTopic.CONTENT_UPDATE
