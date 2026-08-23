"""search_x: paid X (Twitter) recent-search, added 2026-08-21 after the top suggest_tool gap (15 of 82 requests tonight, dwarfing every other capability) turned out to be exactly this. Opt-in (X_SEARCH_ENABLED + X_BEARER_TOKEN both required) and rationed three ways: a fixed 10-result max_results (X's own API minimum, the model can't ask for more), a daily Redis-backed call budget shared across every article composed that day, and a per-session cap in llm_openai_compatible.py's _CALL_CAPPED_TOOLS."""

from __future__ import annotations

import httpx
import pytest

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
    monkeypatch: pytest.MonkeyPatch, fake_redis: object
) -> None:
    """A configured, in-budget call returns full post text (billed per result -- truncating after the fact saves nothing, see the tool's own docstring) and daily-usage counters."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("app.core.config.X_SEARCH_DAILY_CAP", 20)
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)

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
    # Real engagement present -- no need to warn the writer off overstating it.
    assert "engagement_note" not in result


def test_low_engagement_results_get_a_framing_note(
    monkeypatch: pytest.MonkeyPatch, fake_redis: object
) -> None:
    """Every result under 3 combined likes/reposts/replies triggers a note telling the writer not to frame an isolated, unengaged post as a broader reaction -- root-caused 2026-08-21 (HesabPay/Movement article), where a single 0-engagement reply got cited as 'Algorand community members noticed'."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)

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
    monkeypatch: pytest.MonkeyPatch, fake_redis: object
) -> None:
    """Once the daily budget is exhausted, further calls are refused before any HTTP request (and therefore before any cost) is incurred."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("app.core.config.X_SEARCH_DAILY_CAP", 2)
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)

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
    assert calls["n"] == 2  # the third call never reached the network


def test_budget_key_is_scoped_to_todays_utc_date(
    monkeypatch: pytest.MonkeyPatch, fake_redis: object
) -> None:
    """The Redis counter key is dated (news:x_search_count:YYYY-MM-DD, today's real UTC date) -- confirms the budget resets per calendar day rather than being a single global counter."""
    import datetime

    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, _SEARCH_PAYLOAD),
    )

    assert "error" not in _tool_search_x("first")
    today = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
    assert fake_redis.store.get(f"news:x_search_count:{today}") == "1"


def test_network_failure_reports_error_not_crash(
    monkeypatch: pytest.MonkeyPatch, fake_redis: object
) -> None:
    """A network/HTTP failure degrades to an error dict, never an unhandled exception."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake_redis)

    def fake_get(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    result = _tool_search_x("algorand")
    assert "error" in result


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
