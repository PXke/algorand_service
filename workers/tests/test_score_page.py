"""Explorer-link and keyword signals in the relevance scorer."""

from __future__ import annotations

from app.modules.search.classifier.score import score_page


def test_outbound_explorer_link_clears_threshold_with_no_keyword_text() -> None:
    """Scores a chain-agnostic page in-scope on an outbound explorer link alone, with no chain keywords in its own text."""
    # Root cause 2026-07-21: quantoz.com's EURQ product page links straight to
    # its allo.info ASA page but never uses the word "algorand" in its own
    # prose (EURQ is also issued on Ethereum/XRPL/Stellar/Xahau, so the
    # marketing copy stays chain-agnostic). Keyword text scoring alone misses
    # this domain entirely; the explorer-link signal must catch it.
    text = "Digital euros and dollars for global commerce. No chain names here."
    result = score_page(
        url="https://quantoz.com/products/eurq-usdq",
        text=text,
        outbound_links=("https://allo.info/asset/2768422954/token",),
    )
    assert result.in_scope
    assert any(r.startswith("links_to_explorer:") for r in result.reasons)


def test_unrelated_outbound_links_do_not_trigger_the_signal() -> None:
    """Does not trigger the explorer-link signal for outbound links to non-explorer sites."""
    result = score_page(
        url="https://example.com",
        text="Nothing chain-related here.",
        outbound_links=("https://etherscan.io/address/0x1", "https://twitter.com/example"),
    )
    assert not result.in_scope
    assert not any(r.startswith("links_to_explorer:") for r in result.reasons)


def test_no_outbound_links_is_a_pure_noop() -> None:
    """Produces identical results whether outbound_links is omitted or passed as empty."""
    # Default is empty — existing callers (index_crawled_page) that don't pass
    # outbound_links must see identical behavior to before this signal existed.
    with_default = score_page(url="https://example.com", text="algorand defi")
    explicit_empty = score_page(url="https://example.com", text="algorand defi", outbound_links=())
    assert with_default == explicit_empty


def test_repeated_keyword_scores_higher_than_single_mention() -> None:
    """Scores repeated keyword mentions higher than a single mention instead of scoring presence-only."""
    # Root cause 2026-07-24 (urvote.ca): the priority scorer's own keyword
    # count was presence-only (same bug as keyword_hits() in the same
    # module) — a page saying "algorand" repeatedly in body copy scored
    # identically to one that name-drops it once. A page unknown to
    # KNOWN_DOMAINS/ecosystem listings with no outbound explorer link so the
    # keyword-repetition signal alone is isolated.
    single = score_page(
        url="https://urvote-test.example",
        text="Built on Algorand for full transparency.",
    )
    repeated = score_page(
        url="https://urvote-test.example",
        text=(
            "Elections designed to be secure, flexible, and reliable. "
            "Every vote is recorded on the Algorand blockchain. "
            "Built on Algorand for complete transparency and trust."
        ),
    )
    assert repeated.score > single.score
    assert not single.in_scope
    assert repeated.in_scope
