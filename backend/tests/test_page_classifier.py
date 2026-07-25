"""Keyword relevance classifier accepts on-topic pages and rejects noise."""

from __future__ import annotations

from app.modules.search.classifier.score import score_page


def test_classifier_accepts_algorand_foundation_page() -> None:
    """Scores an on-topic Algorand Foundation page as in-scope with a high relevance score."""
    result = score_page(
        url="https://algorand.foundation/about",
        text="Algorand is a pure proof of stake blockchain. Learn about ALGO and DeFi on TestNet.",
    )
    assert result.in_scope
    assert result.score >= 0.35


def test_classifier_rejects_off_topic_algorithm_noise() -> None:
    """Rejects a page whose text merely mentions "algorithm" without Algorand relevance."""
    result = score_page(
        url="https://example.com/blog",
        text="This article explains a sorting algorithm for algebra homework.",
    )
    assert not result.in_scope


def test_classifier_boosts_known_domain() -> None:
    """Marks a known-domain URL (perawallet.app) in-scope via a known_domain reason."""
    result = score_page(
        url="https://perawallet.app/help",
        text="Wallet setup guide.",
    )
    assert result.in_scope
    assert any(r.startswith("known_domain:") for r in result.reasons)
