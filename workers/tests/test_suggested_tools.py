"""Coverage for the 2026-07-08 tool-insights follow-ups: suggest_tool self-correction (the writer kept suggesting tools it already has — the top prod asks reddit_api_post_history/discourse_forum/medium_api_article_list were all long since registered), discourse_forum search (the real ask was "search the forum for <project>", which latest-topics can't answer), and the new xgov_proposal_status tool (asked for by name in prod)."""

from typing import Any

import pytest

import app.modules.ai.research_tools as rt
import app.modules.ai.writer_tools as wt


class _FakeResp:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str = "") -> None:  # noqa: ANN401 -- arbitrary JSON test fixture
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:  # noqa: ANN401 -- arbitrary JSON test fixture
        return self._payload


# ---------------------------------------------------------------- suggest_tool

_KNOWN = {
    "discourse_forum",
    "reddit_api_post_history",
    "fetch_archive_text",
    "search_bluesky",
    "lookup_account",
    "github_repository_contents",
    "suggest_tool",
}


def test_match_covers_the_actual_prod_suggestions() -> None:
    """Real prod-observed tool suggestions all resolve to an already-registered equivalent tool."""
    match = wt._match_existing_tool
    assert match("reddit_api_post_history", _KNOWN) == "reddit_api_post_history"
    assert match("discourse_api", _KNOWN) == "discourse_forum"
    assert match("wayback_machine_full_text", _KNOWN) == "fetch_archive_text"
    assert match("algo_account_lookup", _KNOWN) == "lookup_account"


_NFT_KNOWN = _KNOWN | {
    "nft_asset_listing_status",
    "nft_collection_market_stats",
    "nft_collection_distribution_timeline",
}


def test_marketplace_health_ask_does_not_false_match_a_single_item_or_stats_tool() -> None:
    """2026-08-28 rejected-article audit: a suggestion asking whether a whole
    marketplace is up/operational must NOT fuzzy-match nft_asset_listing_status
    (a single asset ID's listing state) or nft_collection_market_stats (a
    collection's price/volume stats) on the shared-but-domain-generic "nft"
    token alone -- both are a different question than marketplace liveness."""
    match = wt._match_existing_tool
    assert match("algorand_nft_marketplace_health_aggregator", _NFT_KNOWN) is None


def test_nft_alone_is_too_generic_to_match_across_unrelated_nft_tools() -> None:
    """Sharing only "nft" -- a token every registered NFT tool's name contains --
    with an otherwise unrelated capability ask must never be treated as a match,
    the same way a shared "google" or "api" token isn't."""
    match = wt._match_existing_tool
    assert match("nft_royalty_enforcement_checker", _NFT_KNOWN) is None
    assert match("nft_mint_event_stream", _NFT_KNOWN) is None


def test_close_nft_rewording_still_matches_on_multiple_shared_tokens() -> None:
    """A genuinely close rewording (multiple shared significant tokens, not just
    the generic "nft") still redirects to the existing tool -- the fix must not
    make every NFT-domain suggestion an unconditional miss."""
    match = wt._match_existing_tool
    assert match("nft_asset_listing_lookup", _NFT_KNOWN) == "nft_asset_listing_status"
    assert match("nft_collection_market_data", _NFT_KNOWN) == "nft_collection_market_stats"


def test_single_generic_token_overlap_never_matches() -> None:
    """A capability sharing only a clearly-generic token (already excluded from
    "significant") with an otherwise unrelated tool never matches -- generic
    tokens carry zero signal regardless of overlap ratio."""
    match = wt._match_existing_tool
    assert match("widget_report_api", _KNOWN | {"gizmo_export_api"}) is None


def test_twitter_x_alias_targets_search_x_not_bluesky() -> None:
    """The twitter/x alias points at search_x (added 2026-08-21), not search_bluesky -- misdirecting a genuine X-only need to the wrong platform's search tool was worse than reporting a real gap."""
    match = wt._match_existing_tool
    # search_x not registered in this session (opt-in, most sessions won't have it) -> honest gap.
    assert match("twitter_x_search", _KNOWN) is None
    # search_x registered this session -> resolves to it, not to search_bluesky.
    assert match("twitter_x_search", _KNOWN | {"search_x"}) == "search_x"


def test_match_leaves_genuine_gaps_alone() -> None:
    """A suggestion for a genuinely missing capability, or an empty string, does not match any existing tool."""
    match = wt._match_existing_tool
    assert match("telegram_channel_search", _KNOWN) is None
    assert match("nft_collection_floor_price", _KNOWN) is None
    assert match("", _KNOWN) is None


def test_handler_nudges_instead_of_recording_when_tool_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the suggested capability already exists, the handler nudges the caller toward it instead of recording a gap."""
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **_k: recorded.append(a) or True,
    )
    handler = wt._make_suggest_tool_handler({}, known_tools=_KNOWN)
    out = handler(capability="discourse_api", reason="want forum data")
    assert out["already_available"] == "discourse_forum"
    assert "call discourse_forum" in out["hint"]
    assert not recorded


def test_handler_still_records_genuine_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely missing capability is still recorded as a tool-insight suggestion."""
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **_k: recorded.append(a) or True,
    )
    handler = wt._make_suggest_tool_handler({}, known_tools=_KNOWN)
    out = handler(capability="telegram_channel_search", reason="no telegram access")
    assert out == {"ok": True, "noted": "telegram_channel_search"}
    assert len(recorded) == 1


def test_all_tools_wires_the_full_registry_into_suggest(monkeypatch: pytest.MonkeyPatch) -> None:
    """suggest_tool must be registered AFTER every toolset merges, so suggesting an existing research tool self-corrects."""
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **_k: recorded.append(a) or True,
    )
    _, handlers = wt.all_tools()
    out = handlers["suggest_tool"](capability="github_repository_contents", reason="")
    assert out.get("already_available") == "github_repository_contents"
    assert not recorded


def test_all_tools_does_not_nudge_toward_a_schema_less_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The already-have-it check must reflect what's actually OFFERED (schema names), not the full handlers registry -- reddit_api_post_history has a handler (a truthful stub for stale references) but deliberately no schema since 2026-07-16 (Reddit 403s this server). Confirmed live 2026-08-07 on a VibeKit compose: it asked for reddit_search, got told 'already_available: reddit_api_post_history', and that name was never in its own callable tool list."""
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **_k: recorded.append(a) or True,
    )
    schemas, handlers = wt.all_tools()
    assert "reddit_api_post_history" in handlers  # the stub is still registered...
    assert not any(
        s["function"]["name"] == "reddit_api_post_history" for s in schemas
    )  # ...but never offered
    out = handlers["suggest_tool"](capability="reddit_search", reason="want community sentiment")
    assert "already_available" not in out
    assert out == {"ok": True, "noted": "reddit_search"}
    assert len(recorded) == 1


# ------------------------------------------------------- discourse_forum search

_ABOUT = {"about": {"title": "Algorand Forum", "description": "d", "stats": {"topic_count": 5}}}
_SEARCH = {
    "posts": [
        {
            "topic_id": 42,
            "blurb": "rug.ninja bonding curve...",
            "username": "alice",
            "created_at": "2026-07-01T10:00:00Z",
        },
    ],
    "topics": [
        {"id": 42, "title": "Help understanding rug.ninja", "slug": "rug-ninja", "posts_count": 7}
    ],
}


def test_discourse_query_searches_instead_of_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A discourse_forum call with a query hits the search endpoint and returns matching results, not a topic listing."""
    calls = []

    def fake_get(
        url: str,
        *,
        headers: dict | None = None,  # noqa: ARG001 -- name must match the real callee's keyword arg
        params: tuple | None = None,
        timeout: float = 12.0,  # noqa: ARG001 -- name must match the real callee's keyword arg
    ) -> _FakeResp:
        calls.append((url, params))
        if url.endswith("/about.json"):
            return _FakeResp(_ABOUT)
        if url.endswith("/search.json"):
            return _FakeResp(_SEARCH)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(rt, "_guarded_get", fake_get)
    out = rt._tool_discourse_forum("https://forum.algorand.org", query="rug.ninja")
    assert out["query"] == "rug.ninja"
    assert out["count"] == 1
    hit = out["results"][0]
    assert hit["topic"] == "Help understanding rug.ninja"
    assert hit["url"] == "https://forum.algorand.org/t/rug-ninja/42"
    assert hit["date"] == "2026-07-01"
    # Search must not also pull categories/latest (payload stays small).
    assert not any("/latest.json" in u or "/categories.json" in u for u, _ in calls)


def test_discourse_without_query_keeps_listing_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """A discourse_forum call without a query falls back to the original recent-topics listing behavior."""

    def fake_get(
        url: str,
        *,
        headers: dict | None = None,  # noqa: ARG001 -- name must match the real callee's keyword arg
        params: tuple | None = None,  # noqa: ARG001 -- name must match the real callee's keyword arg
        timeout: float = 12.0,  # noqa: ARG001 -- name must match the real callee's keyword arg
    ) -> _FakeResp:
        if url.endswith("/about.json"):
            return _FakeResp(_ABOUT)
        if url.endswith("/categories.json"):
            return _FakeResp({"category_list": {"categories": []}})
        if url.endswith("/latest.json"):
            return _FakeResp({"topic_list": {"topics": []}})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(rt, "_guarded_get", fake_get)
    out = rt._tool_discourse_forum("https://forum.algorand.org")
    assert "results" not in out
    assert out["recent_topics"] == []


# --------------------------------------------------------- xgov_proposal_status

_PROPOSAL_MD = """---
id: 100
period: 3
title: GPU-based vanity address generator for Algorand
author: Marcin Zawiejski (@dragmz)
discussions-to: https://forum.algorand.org/t/xgov-100/11067
category: Tools
amount_requested: 47474
status: Approved
---

## Abstract
A tool for generating Algorand vanity addresses using GPU acceleration.

## Team
- someone
"""


def test_xgov_single_proposal_parses_frontmatter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetching a single xGov proposal parses its YAML frontmatter and markdown abstract."""
    monkeypatch.setattr(rt, "_guarded_get", lambda _url, **_k: _FakeResp(text=_PROPOSAL_MD))
    out = rt._tool_xgov_proposal(proposal_id=100)
    assert out["title"] == "GPU-based vanity address generator for Algorand"
    assert out["status"] == "Approved"
    assert out["amount_requested_algo"] == "47474"
    assert out["abstract"].startswith("A tool for generating Algorand vanity")
    assert out["url"].endswith("/Proposals/xgov-100.md")


def test_xgov_unknown_id_explains_id_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prod suggestion cited on-chain app ids (3572597746) as 'proposal ids' — the miss must teach the model the difference."""
    monkeypatch.setattr(rt, "_guarded_get", lambda _url, **_k: _FakeResp(status_code=404))
    out = rt._tool_xgov_proposal(proposal_id=3572597746)
    assert "lookup_application" in out["error"]


def test_xgov_listing_returns_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Listing xGov proposals returns them ordered newest-id-first, limited, without fetching abstracts."""
    listing = [{"name": f"xgov-{i}.md"} for i in (1, 100, 27)] + [{"name": "README.md"}]

    def fake_get(url: str, **_k: object) -> _FakeResp:
        if url.endswith("/Proposals"):
            return _FakeResp(listing)
        return _FakeResp(text=_PROPOSAL_MD)

    monkeypatch.setattr(rt, "_guarded_get", fake_get)
    out = rt._tool_xgov_proposal(limit=2)
    assert out["total_proposals"] == 3
    assert [p["id"] for p in out["proposals"]] == [100, 27]
    assert all("abstract" not in p for p in out["proposals"])


def test_xgov_registered_in_research_tools() -> None:
    """The xgov_proposal_status tool is registered in both the research schemas and handlers."""
    schemas, handlers = rt.research_tools()
    assert "xgov_proposal_status" in handlers
    assert any(s["function"]["name"] == "xgov_proposal_status" for s in schemas)
