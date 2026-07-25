"""Frozen source fixtures for scripts/eval_compose_prompts.py.

Each fixture pins the exact `compose_scrape_article_mistral` inputs for one
realistic scenario the compose prompt has to get right. Keep this list SMALL
(5-10) and STABLE — the point is a fixed input so the only thing that changes
between two runs is the prompt, making outputs diffable. Add a fixture only
when a real failure mode needs a permanent regression check; don't grow this
into a general test corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ComposeFixture:
    """One fixture case for the offline compose-prompt evaluation harness."""
    name: str
    service_name: str
    source_url: str
    page_title: str
    page_text: str
    txid: str = "AAAAFIXTURETXID000000000000000000000000000000000000000"
    round_num: int = 40_000_000
    diff: str | None = None
    is_first_snapshot: bool = True
    # What to eyeball in the diff — not enforced, just a reminder while reading.
    watch_for: str = field(default="")


FIXTURES: tuple[ComposeFixture, ...] = (
    ComposeFixture(
        name="protocol_upgrade_dated",
        service_name="Tinyman",
        source_url="https://tinyman.org/blog/v3-liquidity-upgrade",
        page_title="Tinyman v3: Concentrated Liquidity Is Live",
        page_text=(
            "Posted 2026-06-15. Tinyman v3 introduces concentrated liquidity pools, "
            "letting liquidity providers set custom price ranges instead of spreading "
            "capital across the full curve. Early pools show a 3.2x improvement in "
            "capital efficiency for the ALGO/USDC pair versus v2. The upgrade also "
            "cuts the swap fee floor from 0.3% to 0.05% for stable pairs. Migration "
            "from v2 pools is opt-in and will run through Q3 2026."
        ),
        watch_for="dated announcement -> not breaking news months later; no invented current price",
    ),
    ComposeFixture(
        name="static_landing_page",
        service_name="Folks Finance",
        source_url="https://folks.finance/",
        page_title="Folks Finance — Lending and Liquid Staking on Algorand",
        page_text=(
            "Folks Finance is a decentralized lending and borrowing protocol on "
            "Algorand. Users can supply assets to earn yield, borrow against "
            "collateral, and stake ALGO for liquid governance tokens. The protocol "
            "also offers cross-chain lending via its xAlgo product. Roadmap: expand "
            "to additional chains, launch a fixed-rate lending market."
        ),
        watch_for="root-domain landing page -> STATIC PROFILE MODE, evergreen present tense",
    ),
    ComposeFixture(
        name="partnership_thin_content",
        service_name="Algorand Foundation",
        source_url="https://algorand.co/news/foundation-partners-with-payments-firm",
        page_title="Algorand Foundation Announces Partnership",
        page_text=(
            "The Algorand Foundation today announced a partnership with a regional "
            "payments processor to explore stablecoin settlement rails. Terms were "
            "not disclosed. More details to follow."
        ),
        watch_for="thin source -> short honest piece, no padding, no invented figures",
    ),
    ComposeFixture(
        name="governance_dense_numbers",
        service_name="Algorand Governance",
        source_url="https://governance.algorand.foundation/proposals/xgov-period-12",
        page_title="xGov Period 12 Results",
        page_text=(
            "xGov Period 12 concluded with 143 proposals submitted, 61 approved, and "
            "a total allocation of 4.2M ALGO. Approved categories: DeFi tooling "
            "(18 proposals, 1.4M ALGO), developer education (12 proposals, 900K "
            "ALGO), community events (14 proposals, 650K ALGO), infrastructure "
            "(9 proposals, 780K ALGO), other (8 proposals, 470K ALGO). Voter "
            "turnout was 38% of eligible governors, up from 31% last period."
        ),
        watch_for="dense stats -> should land in a table/list, not one dense paragraph",
    ),
    ComposeFixture(
        name="technical_sdk_release",
        service_name="Algorand Developer Portal",
        source_url="https://developer.algorand.org/news/algokit-4-release",
        page_title="AlgoKit 4.0 Released",
        page_text=(
            "AlgoKit 4.0 ships a new TypeScript client generator for ARC-56 "
            "contracts, box storage helpers, and a local sandbox that boots in "
            "under 5 seconds. The client generator produces fully typed methods "
            "from a contract's ABI, removing a class of runtime encoding bugs "
            "developers previously hit when calling smart contracts manually."
        ),
        watch_for="translate technical findings -> explain what ARC-56 clients ENABLE",
    ),
    ComposeFixture(
        name="stale_source_old_figures",
        service_name="DeFi Pulse Algorand",
        source_url="https://example-defi-tracker.io/algorand/tvl-report",
        page_title="Algorand DeFi TVL Report",
        page_text=(
            "As of March 2022, total value locked across Algorand DeFi protocols "
            "reached $450M, led by Tinyman ($180M) and AlgoFi ($120M). Growth was "
            "attributed to the launch of ASA rewards programs."
        ),
        is_first_snapshot=False,
        diff=None,
        watch_for="2022 figures -> must be attributed as historical, never presented as current",
    ),
    ComposeFixture(
        name="general_news_no_price_bait",
        service_name="Algorand NFT Marketplace",
        source_url="https://example-nft-market.io/news/new-collection-launch",
        page_title="New Generative Art Collection Launches",
        page_text=(
            "A new generative art collection of 5,000 pieces launched today on an "
            "Algorand-based NFT marketplace, with mint proceeds split 80/20 between "
            "the artist and a community grants pool. The collection sold out its "
            "public mint within 40 minutes."
        ),
        watch_for="ALGO PRICE/MARKET RULE -> no price chart in an unrelated NFT story",
    ),
)


def get(name: str) -> ComposeFixture:
    """Look up a named compose fixture, raising KeyError if it doesn't exist."""
    for fx in FIXTURES:
        if fx.name == name:
            return fx
    raise KeyError(f"no such fixture: {name!r}; known: {[f.name for f in FIXTURES]}")
