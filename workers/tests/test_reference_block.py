"""Appending a Sources block of fetched research URLs to a draft."""

from __future__ import annotations

from app.modules.ai.reference_block import (
    _search_result_sources,
    append_reference_block,
    fetched_sources,
)


def _trace() -> list[dict]:
    return [
        {"tool": "search_web", "arguments": {"query": "algoanna"}, "result": {"items": []}},
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://lending.algoanna.com/faq"},
            "result": {"url": "https://lending.algoanna.com/faq", "title": "AlgoAnna Lending FAQ"},
        },
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://algoanna.com"},
            "result": {"url": "https://algoanna.com", "title": "AlgoAnna"},
        },
        {  # failed fetch — must be excluded
            "tool": "fetch_url",
            "arguments": {"url": "https://broken.example"},
            "result": {"url": "https://broken.example", "error": "timeout"},
        },
    ]


def test_fetched_sources_keeps_only_successful_unique() -> None:
    """Keeps only successful, unique fetch_url results and drops the failed one."""
    src = fetched_sources(_trace())
    assert src == [
        ("https://lending.algoanna.com/faq", "AlgoAnna Lending FAQ"),
        ("https://algoanna.com", "AlgoAnna"),
    ]


def test_appends_missing_deep_url_under_new_section() -> None:
    """Adds a fetched deep URL to a new Sources section without duplicating the already-cited domain."""
    # Body cites only the main domain (the reported failure mode).
    body = "Intro paragraph.\n\nSee [AlgoAnna](https://algoanna.com) for more."
    out = append_reference_block({"body": body}, _trace())
    assert "## Sources" in out["body"]
    # The deep URL the model dropped is now present...
    assert "https://lending.algoanna.com/faq" in out["body"]
    # ...and the already-cited main domain isn't duplicated in the block.
    assert out["body"].count("https://algoanna.com)") == 1


def test_merges_into_existing_sources_section_without_duplicate_heading() -> None:
    """Merges missing fetched URLs into an existing "## Sources" section instead of adding a duplicate heading."""
    body = "Body.\n\n## Sources\n\n- [AlgoAnna](https://algoanna.com)"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"].count("## Sources") == 1
    assert "https://lending.algoanna.com/faq" in out["body"]


def test_noop_when_all_urls_already_cited() -> None:
    """Leaves the body unchanged when every fetched URL is already cited in the text."""
    body = "x https://algoanna.com y https://lending.algoanna.com/faq z"
    out = append_reference_block({"body": body}, _trace())
    assert out["body"] == body  # unchanged, no empty Sources block


def test_noop_without_fetches_or_body() -> None:
    """Leaves the body unchanged when there is no fetch trace or the body is empty."""
    assert append_reference_block({"body": "hello"}, [])["body"] == "hello"
    assert append_reference_block({"body": ""}, _trace())["body"] == ""


def _search_trace() -> list[dict]:
    return [
        {
            "tool": "search_web",
            "arguments": {"query": "Bruno Martins Algorand CTO"},
            "result": {
                "query": "Bruno Martins Algorand CTO",
                "results": [
                    {
                        "title": "Algorand plans broad quantum resilience by 2027",
                        "url": "https://www.msn.com/en-us/technology/blockchain/algorand-plans-broad-quantum-resilience-by-2027/ar-AA262424",
                        "snippet": "...",
                    },
                    {
                        # A hit from a domain the body never cites — must NOT
                        # be pulled in just because it showed up in search.
                        "title": "Unrelated coverage",
                        "url": "https://unrelated.example/algorand",
                        "snippet": "...",
                    },
                ],
            },
        },
    ]


def test_backfills_search_hit_for_a_domain_the_body_already_cites() -> None:
    """Replaces a bare-domain citation with the deep link from a matching search-hit result."""
    # Root cause of the broken MSN citation (2026-07-21): the model only ever
    # saw this article as a search snippet, then wrote just the bare domain
    # in its own footer instead of the deep link the snippet actually had.
    body = (
        "Body citing MSN.\n\n## Source\n"
        "- [MSN: Algorand's Quantum Resilience Roadmap](https://www.msn.com)"
    )
    out = append_reference_block({"body": body}, _search_trace())
    assert (
        "https://www.msn.com/en-us/technology/blockchain/"
        "algorand-plans-broad-quantum-resilience-by-2027/ar-AA262424" in out["body"]
    )
    # The unrelated domain the model never cited must not be pulled in.
    assert "unrelated.example" not in out["body"]


def test_does_not_backfill_search_hits_for_uncited_domains() -> None:
    """Adds nothing when no domain in the body matches any search-hit result."""
    # No domain in the body matches any search hit — nothing should be added.
    body = "Body that cites nothing from search results."
    out = append_reference_block({"body": body}, _search_trace())
    assert out["body"] == body


# --- 2026-09-08: fetched_sources extended past fetch_url/search_web --------


def test_fetched_sources_recognizes_github_activity_repo_url() -> None:
    """github_activity's own repo url is a citable source."""
    trace = [
        {
            "tool": "github_activity",
            "arguments": {"repo": "algorand/go-algorand"},
            "result": {
                "repo": "algorand/go-algorand",
                "url": "https://github.com/algorand/go-algorand",
                "stars": 1200,
            },
        },
    ]
    assert fetched_sources(trace) == [("https://github.com/algorand/go-algorand", "github.com")]


def test_fetched_sources_recognizes_defillama_protocol_url() -> None:
    """get_defi_tvl's DeFiLlama protocol page is a citable source."""
    trace = [
        {
            "tool": "get_defi_tvl",
            "arguments": {"protocol": "tinyman"},
            "result": {
                "protocol": "tinyman",
                "tvl_usd": 1_000_000,
                "url": "https://defillama.com/protocol/tinyman",
            },
        },
    ]
    assert fetched_sources(trace) == [("https://defillama.com/protocol/tinyman", "defillama.com")]


def test_fetched_sources_recognizes_package_download_stats_url() -> None:
    """package_download_stats' npm/PyPI package page is a citable source."""
    trace = [
        {
            "tool": "package_download_stats",
            "arguments": {"registry": "npm", "package": "algosdk"},
            "result": {
                "registry": "npm",
                "package": "algosdk",
                "url": "https://www.npmjs.com/package/algosdk",
            },
        },
    ]
    assert fetched_sources(trace) == [("https://www.npmjs.com/package/algosdk", "www.npmjs.com")]


def test_fetched_sources_prefers_nfd_profile_url_over_owner_declared_site() -> None:
    """search_nfd_directory's citable source is nfd_profile_url, NOT the "url" field (that's the NFD owner's self-declared site, not the lookup's own citation)."""
    trace = [
        {
            "tool": "search_nfd_directory",
            "arguments": {"name": "gazer.algo"},
            "result": {
                "name": "gazer.algo",
                "url": "https://gazer.example",  # owner-declared, must NOT be picked
                "nfd_profile_url": "https://app.nf.domains/name/gazer.algo",
            },
        },
    ]
    assert fetched_sources(trace) == [("https://app.nf.domains/name/gazer.algo", "app.nf.domains")]


def test_fetched_sources_ignores_a_tool_not_in_the_registry() -> None:
    """A tool with no entry in _FETCH_TOOLS is silently ignored, even if its result happens to carry a "url" key."""
    trace = [
        {
            "tool": "some_unrelated_tool",
            "arguments": {},
            "result": {"url": "https://example.test/should-not-appear"},
        },
    ]
    assert fetched_sources(trace) == []


def _x_search_trace() -> list[dict]:
    return [
        {
            "tool": "search_x",
            "arguments": {"query": "AlgoChess"},
            "result": {
                "query": "AlgoChess",
                "posts": [
                    {
                        "text": "AlgoChess relaunch is live, staked blitz chess on Algorand!",
                        "likes": 12,
                        "url": "https://x.com/i/web/status/1234567890",
                    },
                    {
                        "text": "Unrelated post the body never cites.",
                        "likes": 0,
                        "url": "https://x.com/i/web/status/9999999999",
                    },
                ],
            },
        },
    ]


def test_search_result_sources_reads_x_posts_key_not_results() -> None:
    """search_x's items live under "posts", not "results" — the generic search-shape reader must still find them."""
    hits = _search_result_sources(_x_search_trace())
    assert (
        "https://x.com/i/web/status/1234567890",
        "AlgoChess relaunch is live, staked blitz chess on Algorand!",
    ) in hits
    assert ("https://x.com/i/web/status/9999999999", "Unrelated post the body never cites.") in hits


def test_backfills_x_post_for_a_domain_the_body_already_cites() -> None:
    """A bare 'x.com' citation is upgraded to the real post permalink, same backfill rule as search_web."""
    body = "Body citing X.\n\n## Sources\n- [AlgoChess on X](https://x.com)"
    out = append_reference_block({"body": body}, _x_search_trace())
    assert "https://x.com/i/web/status/1234567890" in out["body"]
    # The unrelated post the model never cited must not be pulled in.
    assert "9999999999" not in out["body"]


def test_search_result_sources_reads_app_store_metrics_results() -> None:
    """app_store_metrics' items use "app_name" as their label field (no "title")."""
    trace = [
        {
            "tool": "app_store_metrics",
            "arguments": {"term": "Pera Wallet"},
            "result": {
                "results": [
                    {
                        "app_name": "Pera Algo Wallet",
                        "rating_count": 5000,
                        "url": "https://apps.apple.com/us/app/pera-algo-wallet/id1459898525",
                    },
                ],
            },
        },
    ]
    hits = _search_result_sources(trace)
    assert hits == [
        ("https://apps.apple.com/us/app/pera-algo-wallet/id1459898525", "Pera Algo Wallet")
    ]


def test_search_result_sources_reads_asset_market_data_assets_key() -> None:
    """lookup_asset_market_data's items live under "assets", labeled by "name" (falling back to "ticker")."""
    trace = [
        {
            "tool": "lookup_asset_market_data",
            "arguments": {"asset_ids": "226701642"},
            "result": {
                "assets": [
                    {
                        "asset_id": 226701642,
                        "name": "Yieldly",
                        "ticker": "YLDY",
                        "price_usd": 0.001,
                        "url": "https://vestige.fi/asset/226701642",
                    },
                ],
            },
        },
    ]
    hits = _search_result_sources(trace)
    assert hits == [("https://vestige.fi/asset/226701642", "Yieldly")]
