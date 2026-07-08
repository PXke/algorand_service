"""Coverage for the 2026-07-08 tool-insights follow-ups: suggest_tool
self-correction (the writer kept suggesting tools it already has — the top
prod asks reddit_api_post_history/discourse_forum/medium_api_article_list were
all long since registered), discourse_forum search (the real ask was "search
the forum for <project>", which latest-topics can't answer), and the new
xgov_proposal_status tool (asked for by name in prod)."""

from typing import Any

import app.modules.ai.research_tools as rt
import app.modules.ai.writer_tools as wt


class _FakeResp:
    def __init__(self, payload: Any = None, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
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
    match = wt._match_existing_tool
    assert match("reddit_api_post_history", _KNOWN) == "reddit_api_post_history"
    assert match("discourse_api", _KNOWN) == "discourse_forum"
    assert match("wayback_machine_full_text", _KNOWN) == "fetch_archive_text"
    assert match("twitter_x_search", _KNOWN) == "search_bluesky"
    assert match("algo_account_lookup", _KNOWN) == "lookup_account"


def test_match_leaves_genuine_gaps_alone() -> None:
    match = wt._match_existing_tool
    assert match("telegram_channel_search", _KNOWN) is None
    assert match("nft_collection_floor_price", _KNOWN) is None
    assert match("", _KNOWN) is None


def test_handler_nudges_instead_of_recording_when_tool_exists(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **k: recorded.append(a) or True,
    )
    handler = wt._make_suggest_tool_handler({}, known_tools=_KNOWN)
    out = handler(capability="discourse_api", reason="want forum data")
    assert out["already_available"] == "discourse_forum"
    assert "call discourse_forum" in out["hint"]
    assert not recorded


def test_handler_still_records_genuine_gaps(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **k: recorded.append(a) or True,
    )
    handler = wt._make_suggest_tool_handler({}, known_tools=_KNOWN)
    out = handler(capability="telegram_channel_search", reason="no telegram access")
    assert out == {"ok": True, "noted": "telegram_channel_search"}
    assert len(recorded) == 1


def test_all_tools_wires_the_full_registry_into_suggest(monkeypatch) -> None:
    """suggest_tool must be registered AFTER every toolset merges, so
    suggesting an existing research tool self-corrects."""
    recorded = []
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_tool_suggestion",
        lambda *a, **k: recorded.append(a) or True,
    )
    _, handlers = wt.all_tools()
    out = handlers["suggest_tool"](capability="github_repository_contents", reason="")
    assert out.get("already_available") == "github_repository_contents"
    assert not recorded


# ------------------------------------------------------- discourse_forum search

_ABOUT = {"about": {"title": "Algorand Forum", "description": "d", "stats": {"topic_count": 5}}}
_SEARCH = {
    "posts": [
        {"topic_id": 42, "blurb": "rug.ninja bonding curve...", "username": "alice",
         "created_at": "2026-07-01T10:00:00Z"},
    ],
    "topics": [{"id": 42, "title": "Help understanding rug.ninja", "slug": "rug-ninja",
                "posts_count": 7}],
}


def test_discourse_query_searches_instead_of_listing(monkeypatch) -> None:
    calls = []

    def fake_get(url, *, headers=None, params=None, timeout=12.0):
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


def test_discourse_without_query_keeps_listing_behavior(monkeypatch) -> None:
    def fake_get(url, *, headers=None, params=None, timeout=12.0):
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


def test_xgov_single_proposal_parses_frontmatter(monkeypatch) -> None:
    monkeypatch.setattr(
        rt, "_guarded_get", lambda url, **k: _FakeResp(text=_PROPOSAL_MD)
    )
    out = rt._tool_xgov_proposal(proposal_id=100)
    assert out["title"] == "GPU-based vanity address generator for Algorand"
    assert out["status"] == "Approved"
    assert out["amount_requested_algo"] == "47474"
    assert out["abstract"].startswith("A tool for generating Algorand vanity")
    assert out["url"].endswith("/Proposals/xgov-100.md")


def test_xgov_unknown_id_explains_id_space(monkeypatch) -> None:
    """The prod suggestion cited on-chain app ids (3572597746) as 'proposal
    ids' — the miss must teach the model the difference."""
    monkeypatch.setattr(
        rt, "_guarded_get", lambda url, **k: _FakeResp(status_code=404)
    )
    out = rt._tool_xgov_proposal(proposal_id=3572597746)
    assert "lookup_application" in out["error"]


def test_xgov_listing_returns_newest_first(monkeypatch) -> None:
    listing = [{"name": f"xgov-{i}.md"} for i in (1, 100, 27)] + [{"name": "README.md"}]

    def fake_get(url, **k):
        if url.endswith("/Proposals"):
            return _FakeResp(listing)
        return _FakeResp(text=_PROPOSAL_MD)

    monkeypatch.setattr(rt, "_guarded_get", fake_get)
    out = rt._tool_xgov_proposal(limit=2)
    assert out["total_proposals"] == 3
    assert [p["id"] for p in out["proposals"]] == [100, 27]
    assert all("abstract" not in p for p in out["proposals"])


def test_xgov_registered_in_research_tools() -> None:
    schemas, handlers = rt.research_tools()
    assert "xgov_proposal_status" in handlers
    assert any(s["function"]["name"] == "xgov_proposal_status" for s in schemas)
