"""search_x: reads this week's scheduled X (Twitter) search sweep.

Redesigned 2026-08-25 from a live per-compose API call. Opt-in
(X_SEARCH_ENABLED required to register). The writer tool now only ever
reads the x_search_weekly Cassandra cache via
x_search_store.list_snapshots(); the one remaining live X API call
(_x_search_live) is used solely by the weekly sweep task, never by the
writer at compose time.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.modules.ai.research_tools import _tool_search_x, _x_search_live
from app.modules.ai.research_tools import research_tools as research_tools_fn
from app.modules.newspaper.x_search_store import XSearchSnapshot


def _json_response(url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def _snapshot(
    service_id: str,
    display_name: str,
    *,
    posts: list[dict] | None = None,
    error: str = "",
    swept_at: datetime | None = None,
) -> XSearchSnapshot:
    return XSearchSnapshot(
        service_id=service_id,
        display_name=display_name,
        query=display_name,
        posts=tuple(posts or []),
        swept_at=swept_at or datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
        error=error,
    )


_POSTS = [
    {
        "text": "Folks Finance ships a new liquidation engine.",
        "created_at": "2026-08-20T12:00:00.000Z",
        "likes": 42,
        "reposts": 7,
        "replies": 3,
        "url": "https://x.com/i/web/status/1234567890",
    },
    {
        "text": "Big week for the protocol.",
        "created_at": "2026-08-20T11:00:00.000Z",
        "likes": 5,
        "reposts": 0,
        "replies": 1,
        "url": "https://x.com/i/web/status/1234567891",
    },
]


# --------------------------------------------------------------------- tool (reads the cache)


def test_requires_query() -> None:
    """An empty query is a usage error, not a store lookup."""
    result = _tool_search_x("")
    assert result["posts"] == []


def test_not_configured_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """X_SEARCH_ENABLED off reports unconfigured, no store lookup made."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", False)

    def fake_list_snapshots() -> list[XSearchSnapshot]:
        raise AssertionError("should not have queried the store")

    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots", fake_list_snapshots
    )
    result = _tool_search_x("algorand")
    assert "error" in result
    assert result["posts"] == []


def test_matches_tracked_service_by_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A query naming (part of) a tracked service's display_name matches its stored snapshot and returns its cached posts, untouched by any live call."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    snap = _snapshot("folks-finance", "Folks Finance", posts=_POSTS)
    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots",
        lambda: [snap, _snapshot("tinyman", "Tinyman", posts=[])],
    )

    result = _tool_search_x("Folks Finance liquidations")

    assert "error" not in result
    assert result["matched_service"] == "Folks Finance"
    assert result["count"] == 2
    assert result["posts"][0]["text"] == "Folks Finance ships a new liquidation engine."
    assert result["swept_at"] == "2026-08-24T08:00:00+00:00"
    assert "engagement_note" not in result


def test_no_match_reports_tracked_services_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """A query that names no tracked service reports the gap explicitly and offers a sample of what IS tracked, instead of silently returning nothing."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots",
        lambda: [_snapshot("tinyman", "Tinyman", posts=[])],
    )

    result = _tool_search_x("some totally unrelated topic")

    assert "error" in result
    assert result["posts"] == []
    assert result["tracked_services_sample"] == ["Tinyman"]


def test_low_engagement_results_get_a_framing_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every result under 3 combined likes/reposts/replies triggers a note telling the writer not to frame an isolated, unengaged post as a broader reaction -- root-caused 2026-08-21 (HesabPay/Movement article), where a single 0-engagement reply got cited as 'Algorand community members noticed'. Still applies now that results come from the cache instead of a live call."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    low_engagement = [
        {
            "text": "one reply, no one saw it",
            "created_at": "2026-08-20T12:00:00.000Z",
            "likes": 0,
            "reposts": 0,
            "replies": 0,
            "url": "https://x.com/i/web/status/1",
        }
    ]
    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots",
        lambda: [_snapshot("hesabpay", "HesabPay", posts=low_engagement)],
    )
    result = _tool_search_x("HesabPay reaction")
    assert "engagement_note" in result
    assert "community" in result["engagement_note"]


def test_sweep_error_surfaced_when_no_cached_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A matched service whose last sweep call itself failed (network error, etc.) surfaces that error alongside an empty post list rather than looking like a silent zero-result search."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots",
        lambda: [_snapshot("tinyman", "Tinyman", posts=[], error="ConnectError: boom")],
    )
    result = _tool_search_x("Tinyman")
    assert result["matched_service"] == "Tinyman"
    assert result["posts"] == []
    assert result["sweep_error"] == "ConnectError: boom"


def test_store_failure_degrades_to_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cassandra failure while reading the cache degrades to an error dict, never an unhandled exception."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)

    def fake_list_snapshots() -> list[XSearchSnapshot]:
        raise RuntimeError("cassandra down")

    monkeypatch.setattr(
        "app.modules.newspaper.x_search_store.list_snapshots", fake_list_snapshots
    )
    result = _tool_search_x("Tinyman")
    assert "error" in result
    assert result["posts"] == []


def test_tool_registered_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_x registers in both schemas and handlers only when X_SEARCH_ENABLED is set -- no bearer-token check anymore, since the tool itself never calls X directly (only the sweep task does)."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", False)
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_x" not in names
    assert "search_x" not in handlers

    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_x" in names
    assert "search_x" in handlers


# --------------------------------------------------------------------- _x_search_live (sweep-only)


def test_live_requires_query() -> None:
    """An empty query is a usage error, not an API call."""
    result = _x_search_live("")
    assert result["posts"] == []


def test_live_not_configured_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """X_SEARCH_ENABLED without a bearer token (or vice versa) reports unconfigured, no request made."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "")

    def fake_get(*_a: object, **_kw: object) -> httpx.Response:
        raise AssertionError("should not have made a request")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _x_search_live("algorand")
    assert "error" in result
    assert result["posts"] == []


def test_live_happy_path_full_text_untruncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured live call returns full post text (billed per result -- truncating after the fact saves nothing)."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    payload = {
        "data": [
            {
                "id": "1234567890",
                "text": "Algorand ships v5.0.0 with native post-quantum accounts.",
                "created_at": "2026-08-20T12:00:00.000Z",
                "public_metrics": {"like_count": 42, "retweet_count": 7, "reply_count": 3},
            }
        ]
    }
    seen_params: list[dict] = []

    def fake_get(url: str, **kw: object) -> httpx.Response:
        seen_params.append(kw.get("params") or {})
        return _json_response(url, 200, payload)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _x_search_live("algorand quantum")

    assert "error" not in result
    assert result["count"] == 1
    assert result["posts"][0]["text"] == (
        "Algorand ships v5.0.0 with native post-quantum accounts."
    )
    assert result["posts"][0]["likes"] == 42
    assert result["posts"][0]["url"] == "https://x.com/i/web/status/1234567890"
    # max_results is fixed, never model-adjustable -- cost predictability depends on this.
    assert seen_params[0]["max_results"] == 10


def test_live_network_failure_reports_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network/HTTP failure degrades to an error dict, never an unhandled exception."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    def fake_get(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _x_search_live("algorand")
    assert "error" in result
