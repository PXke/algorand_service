from __future__ import annotations

from app.modules.search.classifier.score import score_page


def test_classifier_accepts_algorand_foundation_page() -> None:
    result = score_page(
        url="https://algorand.foundation/about",
        text="Algorand is a pure proof of stake blockchain. Learn about ALGO and DeFi on TestNet.",
    )
    assert result.in_scope
    assert result.score >= 0.35


def test_classifier_rejects_off_topic_algorithm_noise() -> None:
    result = score_page(
        url="https://example.com/blog",
        text="This article explains a sorting algorithm for algebra homework.",
    )
    assert not result.in_scope


def test_classifier_boosts_known_domain() -> None:
    result = score_page(
        url="https://perawallet.app/help",
        text="Wallet setup guide.",
    )
    assert result.in_scope
    assert any(r.startswith("known_domain:") for r in result.reasons)
