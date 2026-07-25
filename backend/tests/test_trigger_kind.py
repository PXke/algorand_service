"""Classifying which event triggered an article."""

from app.modules.news.services.trigger_kind import classify_article_trigger


def test_weekly_digest_is_scheduled() -> None:
    """Classifies a weekly-price-analysis trigger with a digest txid as scheduled."""
    assert (
        classify_article_trigger(
            service_id="weekly-price-analysis",
            trigger_txid="weekly-digest-2026-W22",
            trigger_round=0,
            source_url="https://www.coingecko.com/en/coins/algorand",
            tags=["weekly", "market"],
        )
        == "scheduled"
    )


def test_chain_trigger() -> None:
    """Classifies a full-length uppercase-alnum Algorand txid as a chain trigger."""
    tx = "A" * 52
    assert (
        classify_article_trigger(
            service_id="algorand-foundation",
            trigger_txid=tx,
            trigger_round=42,
            tags=["web"],
        )
        == "chain"
    )


def test_crawl_is_editorial() -> None:
    """Classifies a short non-chain, non-digest trigger id as editorial (crawl)."""
    assert (
        classify_article_trigger(
            service_id="algorand-foundation",
            trigger_txid="short-id",
            trigger_round=1,
            tags=["web"],
        )
        == "editorial"
    )
