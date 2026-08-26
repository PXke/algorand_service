"""Publish/reject scoring, including the explorer-link relevance signal."""

from __future__ import annotations

import pytest

from app.modules.ai.publish_classifier import (
    is_content_quality_sufficient,
    is_publish_worthy,
    relevance_score,
    score_content_for_storage,
)


def test_score_content_for_storage() -> None:
    """Scores a clearly Algorand-relevant page at or above 5."""
    text = "Algorand mainnet ASA defi on Algorand blockchain"
    score = score_content_for_storage(text, "https://algorand.com")
    assert score >= 5


def test_relevance_score_uses_explorer_link_when_text_says_nothing() -> None:
    """An outbound Algorand-explorer link lifts relevance even when the page text never mentions Algorand."""
    # Root cause 2026-07-22: a multi-chain service's own page text can
    # legitimately never say "algorand" (quantoz.com/EURQ, zerosignal.ai),
    # while linking straight to its Algorand explorer entry. Without
    # outbound_links this scores 0 and the service's publish-queue priority
    # sinks to the bottom even after the SAME domain already cleared
    # discovery on this exact signal.
    text = "A private, encrypted AI protocol. No email, no logging."
    without_links = relevance_score(text, "https://svc.example")
    with_link = relevance_score(
        text,
        "https://svc.example",
        outbound_links=("https://allo.info/asset/999/token",),
    )
    assert without_links == 0.0
    assert with_link > without_links


def test_score_content_for_storage_penalizes_seo_spam_shapes() -> None:
    """A keyword-stuffed price-prediction spam page (the bitnation.co shape, root-caused 2026-08-26) must score meaningfully lower than an equally keyword-dense but organic page."""
    spam_text = (
        "Algorand ALGO Price Prediction 2024, 2025-2030. "
        "Is Algorand a good investment? Should you buy Algorand now? "
        "Algorand mainnet ASA defi testnet algod ppos microalgo"
    )
    organic_text = "Algorand mainnet ASA defi testnet algod ppos microalgo Algorand Algorand"
    spam_score = score_content_for_storage(spam_text)
    organic_score = score_content_for_storage(organic_text)
    assert spam_score < organic_score


def test_score_content_for_storage_never_goes_negative() -> None:
    """Heavy spam on a thin, barely-relevant page must clamp at 0, not go negative."""
    text = "best crypto casino how to buy is it a good investment should you buy 2024-2025"
    assert score_content_for_storage(text) == 0.0


def test_score_content_for_storage_does_not_penalize_legitimate_casino_mention() -> None:
    """A real web3 casino/gaming project's own product description must not be penalized just for using the word "casino" once with no spam framing (rantlabs.xyz, root-caused 2026-08-26): the bare bcasino word-boundary pattern fired on the topic word alone, dragging a legitimate site's score toward 0 the same way the bitnation.co fix targeted actual spam."""
    text = (
        "Rantlabs is a web3 casino platform built on Algorand, offering "
        "provably fair games. Algorand mainnet ASA defi testnet algod ppos microalgo"
    )
    organic_text = (
        "Rantlabs is a web3 gaming platform built on Algorand, offering "
        "provably fair games. Algorand mainnet ASA defi testnet algod ppos microalgo"
    )
    assert score_content_for_storage(text) == score_content_for_storage(organic_text)


def test_score_content_for_storage_still_catches_casino_spam_phrasing() -> None:
    """Genuine gambling-affiliate spam phrasing ("online casino", "casino bonuses") must still be penalized even though the bare topic word no longer is on its own."""
    spam_text = (
        "Top Online Casino Sites Accepting Algorand ALGO in 2026 - Crypto "
        "Casino Bonuses, Free Spins and No Deposit Offers. "
        "Algorand mainnet ASA defi testnet algod ppos microalgo"
    )
    organic_text = "Algorand mainnet ASA defi testnet algod ppos microalgo Algorand Algorand"
    assert score_content_for_storage(spam_text) < score_content_for_storage(organic_text)


def test_score_content_for_storage_bitnation_price_prediction_unaffected() -> None:
    """The original bitnation.co-shaped price-prediction spam that motivated the 2026-08-26 penalty fix is untouched by the casino-pattern precision fix (no "casino" mention at all)."""
    spam_text = (
        "Algorand (ALGO) Price Prediction 2024, 2025-2030. Is Algorand a "
        "good investment? Should you buy Algorand now? How to buy Algorand. "
        "Algorand mainnet ASA defi testnet algod ppos microalgo"
    )
    assert score_content_for_storage(spam_text) == 0.0


def test_score_content_for_storage_uses_explorer_link() -> None:
    """An outbound Algorand-explorer link also lifts the storage-scoring score."""
    text = "A private, encrypted AI protocol. No email, no logging."
    without_links = score_content_for_storage(text, "https://svc.example")
    with_link = score_content_for_storage(
        text,
        "https://svc.example",
        outbound_links=("https://explorer.perawallet.app/asset/999",),
    )
    assert with_link > without_links


def test_is_content_quality_sufficient() -> None:
    """Passes text dense with Algorand terms, fails generic text with none."""
    assert is_content_quality_sufficient("Algorand algo ASA defi mainnet testnet")
    assert not is_content_quality_sufficient("hello world")


def test_is_content_quality_sufficient_repeated_single_family() -> None:
    """Repeated mentions of just 'Algorand' count as real signal, but one incidental mention doesn't."""
    # Root cause 2026-07-24 (urvote.ca): a legitimately Algorand-specific page
    # can say "Algorand" repeatedly in body copy and never say asa/defi/
    # mainnet/testnet/algod/ppos/microalgo/algo. Presence-only counting gave
    # this exactly 1 point regardless of repetition — same as a single
    # incidental mention elsewhere — and failed the floor. Repeated mentions
    # of the flagship term should count as real signal.
    padding = "Elections designed to be secure, flexible, and reliable. " * 5
    text = (
        f"{padding}Every vote is recorded on the Algorand blockchain. "
        "Built on Algorand for complete transparency and trust."
    )
    assert len(text) >= 300
    assert is_content_quality_sufficient(text)
    # A single incidental mention must still fail — repetition is the signal,
    # not mere presence.
    assert not is_content_quality_sufficient(f"{padding}Powered by Algorand.")


def test_sampling_forces_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """A random draw below the sampling threshold forces manual review (returns None)."""
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.1)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.5)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 0.5)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is None


def test_sampling_threshold_one_always_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sampling threshold of 1.0 always forces manual review, regardless of the random draw."""
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.0)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.1)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 1.0)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is None


def test_sampling_allows_auto_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """A random draw above the sampling threshold allows auto-publish (returns True)."""
    import app.modules.ai.publish_classifier as pc

    monkeypatch.setattr(pc.random, "random", lambda: 0.99)
    monkeypatch.setattr("app.core.config.PUBLISH_CLASSIFIER_ENABLED", False)
    monkeypatch.setattr("app.core.config.CLASSIFIER_CONFIDENCE_THRESHOLD", 0.5)
    monkeypatch.setattr("app.core.config.CLASSIFIER_SAMPLING_THRESHOLD", 0.5)
    text = "Algorand mainnet ASA defi wallet algorand foundation"
    result = is_publish_worthy(text, "https://algorand.foundation", "news")
    assert result is True
