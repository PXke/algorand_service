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


def test_generic_multichain_docs_do_not_reach_the_promote_threshold() -> None:
    """A multi-chain-docs page shaped like calnix.gitbook.io — heavy on generic testnet/mainnet/defi/indexer/walletconnect vocabulary, zero Algorand-specific signal — must land below FRONTIER_CONTENT_PROMOTE_SCORE."""
    # Root-caused 2026-08-26: calnix.gitbook.io (real Aave/Ethereum
    # documentation, zero Algorand mentions on live fetch) scored exactly
    # 0.500 — precisely FRONTIER_CONTENT_PROMOTE_SCORE — purely from
    # generic "defi"/"testnet"/"mainnet"-family keyword overlap that applies
    # equally to any chain's own docs. reevaluate_pending_domains' daily
    # beat auto-promotes anything >= that score to an actively monitored
    # service, so this family must never carry a page there alone.
    from app.core.config import FRONTIER_CONTENT_PROMOTE_SCORE

    text = (
        "Deploy your contracts to testnet before mainnet. Connect your wallet "
        "via WalletConnect to interact with the DeFi protocol. Testnet faucets "
        "are available for mainnet parity testing. The indexer tracks all "
        "onchain events across testnet and mainnet deployments. This DeFi "
        "documentation covers lending, borrowing, and liquidation flows on "
        "both testnet and mainnet, with WalletConnect used throughout for "
        "signing. Read the indexer API reference for querying DeFi positions."
    )
    result = score_page(url="https://calnix.gitbook.io/docs/", text=text)
    assert result.score < FRONTIER_CONTENT_PROMOTE_SCORE
    assert not result.in_scope


def test_generic_bitcoin_custody_guide_does_not_reach_the_promote_threshold() -> None:
    """A generic Bitcoin-custody-guide page shaped like protegecoin.com.br — same generic-keyword-family shape, zero Algorand-specific signal — must land below FRONTIER_CONTENT_PROMOTE_SCORE."""
    from app.core.config import FRONTIER_CONTENT_PROMOTE_SCORE

    text = (
        "Learn how to secure your Bitcoin wallet. Test your setup on testnet "
        "before moving funds to mainnet. Our DeFi custody guide explains "
        "multisig, cold storage, and mainnet best practices. Use WalletConnect "
        "to pair your hardware wallet, and check the blockchain indexer to "
        "confirm your testnet and mainnet transactions."
    )
    result = score_page(url="https://protegecoin.com.br/guia-de-custodia", text=text)
    assert result.score < FRONTIER_CONTENT_PROMOTE_SCORE
    assert not result.in_scope


def test_explorer_link_only_page_still_clears_in_scope_after_the_generic_split() -> None:
    """quantoz.com/EURQ-shaped page (explorer link, no chain keywords at all) must still clear in-scope after separating generic from Algorand-specific keyword weight — the explorer-link signal alone is untouched by the split."""
    text = "Digital euros and dollars for global commerce. No chain names here."
    result = score_page(
        url="https://quantoz.com/products/eurq-usdq",
        text=text,
        outbound_links=("https://allo.info/asset/2768422954/token",),
    )
    assert result.in_scope
    assert result.score >= 0.5


def test_genuinely_algorand_page_is_unaffected_by_the_generic_split() -> None:
    """A page that directly mentions 'algorand' alongside generic infra terms is unaffected by splitting generic keywords out of the weighted family — it still clears in-scope comfortably."""
    text = (
        "Algorand mainnet ASA governance staking update for the Algorand "
        "ecosystem, covering testnet deployments and DeFi integrations."
    )
    result = score_page(url="https://example-algorand-news.test", text=text)
    assert result.in_scope
    assert any(r.startswith("keywords:") for r in result.reasons)
    assert any(r == "exact:algorand" for r in result.reasons)
