"""search_x: paid X (Twitter) recent-search, live per-compose call.

Shipped 2026-08-21 after the top suggest_tool gap (15 of 82 requests one
night, dwarfing every other capability) turned out to be exactly this.
Briefly redesigned 2026-08-25 into a weekly-sweep cache read (to stop
per-compose spend); reverted 2026-08-28 back to live calls (owner call: the
composer should be able to query X live within a capped daily budget rather
than being confined to a fixed tracked-service list -- a real story about an
untracked project got nothing from the cache because that project's weekly
sweep had never run). Opt-in (X_SEARCH_ENABLED + X_BEARER_TOKEN both
required) and rationed three ways: a fixed 10-result max_results (X's own
API minimum, the model can't ask for more), a daily Redis-backed call budget
shared across every article composed that day, and a per-session cap in
llm_tool_loop.py's CALL_CAPPED_TOOLS.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
from conftest import FakeRedis

from app.modules.ai.research_tools import _tool_search_x
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


_SEARCH_PAYLOAD = {
    "data": [
        {
            "id": "1234567890",
            "text": "Algorand ships v5.0.0 with native post-quantum accounts.",
            "created_at": "2026-08-20T12:00:00.000Z",
            "public_metrics": {"like_count": 42, "retweet_count": 7, "reply_count": 3},
        },
        {
            "id": "1234567891",
            "text": "Big week for $ALGO.",
            "created_at": "2026-08-20T11:00:00.000Z",
            "public_metrics": {"like_count": 5, "retweet_count": 0, "reply_count": 1},
        },
    ]
}


def test_requires_query() -> None:
    """An empty query is a usage error, not an API call."""
    result = _tool_search_x("")
    assert result["posts"] == []


def test_not_configured_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """X_SEARCH_ENABLED without a bearer token (or vice versa) reports unconfigured, no request made."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "")

    def fake_get(*_a: object, **_kw: object) -> httpx.Response:
        raise AssertionError("should not have made a request")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _tool_search_x("algorand")
    assert "error" in result
    assert result["posts"] == []


def test_happy_path_full_text_untruncated(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """A configured, in-budget call returns full post text (billed per result -- truncating after the fact saves nothing, see the tool's own docstring) and daily-usage counters."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("app.core.config.X_SEARCH_DAILY_CAP", 20)

    seen_params: list[dict] = []

    def fake_get(url: str, **kw: object) -> httpx.Response:
        seen_params.append(kw.get("params") or {})
        return _json_response(url, 200, _SEARCH_PAYLOAD)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _tool_search_x("algorand quantum")

    assert "error" not in result
    assert result["count"] == 2
    assert result["posts"][0]["text"] == (
        "Algorand ships v5.0.0 with native post-quantum accounts."
    )
    assert result["posts"][0]["likes"] == 42
    assert result["posts"][0]["url"] == "https://x.com/i/web/status/1234567890"
    assert result["daily_calls_used"] == 1
    assert result["daily_call_cap"] == 20
    # max_results is fixed, never model-controlled -- cost predictability depends on this.
    assert seen_params[0]["max_results"] == 10
    # 2026-08-28: ranked by relevancy, matching search_bluesky's sort="top",
    # not X's recency default.
    assert seen_params[0]["sort_order"] == "relevancy"
    # Real engagement present -- no need to warn the writer off overstating it.
    assert "engagement_note" not in result


def test_low_engagement_results_get_a_framing_note(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """Every result under 3 combined likes/reposts/replies triggers a note telling the writer not to frame an isolated, unengaged post as a broader reaction -- root-caused 2026-08-21 (HesabPay/Movement article), where a single 0-engagement reply got cited as 'Algorand community members noticed'."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    low_engagement_payload = {
        "data": [
            {
                "id": "1",
                "text": "one reply, no one saw it",
                "created_at": "2026-08-20T12:00:00.000Z",
                "public_metrics": {"like_count": 0, "retweet_count": 0, "reply_count": 0},
            }
        ]
    }
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, low_engagement_payload),
    )
    result = _tool_search_x("some niche query")
    assert "engagement_note" in result
    assert "community" in result["engagement_note"]


def test_daily_cap_refuses_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """Once the daily budget is exhausted, further calls are refused before any HTTP request (and therefore before any cost) is incurred."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("app.core.config.X_SEARCH_DAILY_CAP", 2)

    calls = {"n": 0}

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        calls["n"] += 1
        return _json_response(url, 200, _SEARCH_PAYLOAD)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)

    assert "error" not in _tool_search_x("one")
    assert "error" not in _tool_search_x("two")
    third = _tool_search_x("three")
    assert "error" in third
    assert third["posts"] == []


def test_daily_cap_fails_open_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage on the budget check must not crash the compose or silently eat a session-cap slot for nothing (CLAUDE.md invariant 2.9) -- the call proceeds as if under budget."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    def _raise_redis() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.core.redis_client.get_redis", _raise_redis)
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, _SEARCH_PAYLOAD),
    )

    result = _tool_search_x("algorand")
    assert "error" not in result
    assert result["daily_calls_used"] == 0


def test_budget_key_is_scoped_to_todays_utc_date(
    monkeypatch: pytest.MonkeyPatch, patch_redis_from_url: FakeRedis
) -> None:
    """The Redis counter key is dated (news:x_search_count:YYYY-MM-DD, today's real UTC date) -- confirms the budget resets per calendar day rather than being a single global counter."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, _SEARCH_PAYLOAD),
    )

    assert "error" not in _tool_search_x("first")
    today = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
    assert patch_redis_from_url.store.get(f"news:x_search_count:{today}") == "1"


def test_network_failure_reports_error_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """A network/HTTP failure -- surviving every retry -- degrades to an error dict, never an unhandled exception."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def fake_get(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _tool_search_x("algorand")
    assert "error" in result


def test_transient_502_is_retried_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """502/etc from X's API go through the same _guarded_get_with_retry policy as every other external-API tool (fetch_url, search_bluesky) -- a transient failure doesn't cost the writer its one shot at the topic."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    calls = {"n": 0}

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(502, request=httpx.Request("GET", url))
        return _json_response(url, 200, _SEARCH_PAYLOAD)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _tool_search_x("algorand")
    assert calls["n"] == 2
    assert "error" not in result
    assert result["count"] == 2


def test_tool_registered_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_x registers in both schemas and handlers only when X_SEARCH_ENABLED and X_BEARER_TOKEN are both set -- opt-in, matching every other paid/credentialed tool."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", False)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "")
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_x" not in names
    assert "search_x" not in handlers

    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_x" in names
    assert "search_x" in handlers
