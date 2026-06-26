from __future__ import annotations

from app.modules.ai.publish_classifier import (
    is_content_quality_sufficient,
    is_publish_worthy,
    score_content_for_storage,
)


def test_score_content_for_storage() -> None:
    text = "Algorand mainnet ASA defi on Algorand blockchain"
    score = score_content_for_storage(text, "https://algorand.com")
    assert score >= 5


def test_is_content_quality_sufficient() -> None:
    assert is_content_quality_sufficient("Algorand algo ASA defi mainnet testnet")
    assert not is_content_quality_sufficient("hello world")


def test_sampling_forces_review(monkeypatch) -> None:
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.1)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.5)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 0.5)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is None


def test_sampling_threshold_one_always_review(monkeypatch) -> None:
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.0)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.1)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 1.0)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is None


def test_sampling_allows_auto_publish(monkeypatch) -> None:
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.99)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.5)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 0.5)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is True
