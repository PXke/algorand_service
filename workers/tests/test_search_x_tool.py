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


def test_cache_hit_serves_repeat_query_without_second_live_call(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,
) -> None:
    """A second call with the same query (normalized: different case/whitespace) within the TTL is served from the Redis cache and never re-invokes the live X call -- the actual bug being fixed here: a recompose re-running the writer's research loop from scratch shouldn't re-pay for a question it already asked."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("app.core.config.X_SEARCH_DAILY_CAP", 20)

    calls = {"n": 0}

    def fake_live(q: str) -> dict:
        calls["n"] += 1
        return {
            "query": q,
            "count": 1,
            "posts": [{"text": "hit", "likes": 5, "reposts": 0, "replies": 0, "url": "x"}],
        }

    monkeypatch.setattr("app.modules.ai.research_tools._x_search_live", fake_live)

    first = _tool_search_x("Algorand  Quantum")
    second = _tool_search_x("algorand quantum")  # different case/whitespace, same normalized key

    assert calls["n"] == 1
    assert "error" not in first
    assert "error" not in second
    assert first["posts"] == second["posts"]
    # The cached dict is replayed verbatim, including the counters recorded
    # at the time of the original live call.
    assert second["daily_calls_used"] == first["daily_calls_used"] == 1
    assert patch_redis_from_url.store.get(f"news:x_search_count:{_today()}") == "1"


def test_cache_hit_does_not_consume_daily_budget(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,
) -> None:
    """A cache hit costs zero budget against the shared daily cap -- checked BEFORE `_x_daily_cap_reserve`, not after."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, _SEARCH_PAYLOAD),
    )

    _tool_search_x("algorand")
    _tool_search_x("algorand")
    _tool_search_x("algorand")

    assert patch_redis_from_url.store.get(f"news:x_search_count:{_today()}") == "1"


def test_different_queries_each_call_live_and_consume_budget(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,
) -> None:
    """A cache miss (a genuinely different query) still calls the live API and consumes budget normally -- the cache doesn't accidentally swallow unrelated queries."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    calls = {"n": 0}

    def fake_live(q: str) -> dict:
        calls["n"] += 1
        return {"query": q, "count": 0, "posts": []}

    monkeypatch.setattr("app.modules.ai.research_tools._x_search_live", fake_live)

    _tool_search_x("algorand")
    _tool_search_x("solana")

    assert calls["n"] == 2
    assert patch_redis_from_url.store.get(f"news:x_search_count:{_today()}") == "2"


def test_cache_read_failure_fails_open_to_live_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis being unavailable for the cache check must not block a legitimate live search (CLAUDE.md invariant 2.9) -- it degrades to a cache miss and falls through to the live call, same fail-open contract as `_x_daily_cap_reserve`."""
    import redis

    class _CacheReadFailsRedis(FakeRedis):
        def get(self, key: str) -> str | None:  # noqa: ARG002 -- must match FakeRedis.get's signature
            raise ConnectionError("redis get failed")

    broken = _CacheReadFailsRedis()
    monkeypatch.setattr(redis, "from_url", lambda *_a, **_kw: broken)
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **_kw: _json_response(url, 200, _SEARCH_PAYLOAD),
    )

    result = _tool_search_x("algorand")

    assert "error" not in result
    assert result["count"] == 2


def test_error_result_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """An error result from the live call is never cached (CLAUDE.md invariant 2.8: empty/error is not a cacheable "answer") -- a subsequent call with the same query still attempts a fresh live call, not a replayed error."""
    monkeypatch.setattr("app.core.config.X_SEARCH_ENABLED", True)
    monkeypatch.setattr("app.core.config.X_BEARER_TOKEN", "test-token")

    calls = {"n": 0}

    def fake_live(q: str) -> dict:
        calls["n"] += 1
        return {"query": q, "error": "boom", "posts": []}

    monkeypatch.setattr("app.modules.ai.research_tools._x_search_live", fake_live)

    first = _tool_search_x("algorand")
    second = _tool_search_x("algorand")

    assert "error" in first
    assert "error" in second
    assert calls["n"] == 2


def _today() -> str:
    return datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")


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


# --------------------------------------------------------------------------- #
# Durable cross-recompose reinjection (2026-09-02): a recompose can happen
# days or weeks after the original compose, well past the Redis cache's TTL.
# investigation_findings already durably stores every search_x call a compose
# makes (unmodified by this change); _recompose_via_writer /
# _recompose_published_compose now read it back via
# investigation_store.load_prior_search_x_findings /
# format_prior_search_x_block and hand it to the writer as real usable
# research (enrichment_block), not just a dedup signal. See
# tests/test_investigation_store.py for the pure read/format function tests
# -- these cover the wiring into the two recompose call sites.
# --------------------------------------------------------------------------- #


def test_recompose_via_writer_reinjects_prior_search_x_into_enrichment_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recompose actually gets its own prior search_x findings reinjected as real writer context (enrichment_block), not just a cache-hit-skips-the-call -- the actual owner ask behind this feature."""
    from app.modules.newspaper.tasks import publish_tasks as pt

    monkeypatch.setattr(
        "app.modules.newspaper.admin_source_store.load_active_sources",
        lambda _article_id: [],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_prior_search_x_findings",
        lambda _service_id: [
            {
                "query": "algorand quantum",
                "result": {
                    "posts": [
                        {"text": "Algorand ships v5.0.0", "likes": 42, "reposts": 7, "replies": 3}
                    ]
                },
            }
        ],
    )
    captured: dict = {}

    def _fake_compose(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    pt._recompose_via_writer(
        review_id="rev1",
        url="https://example.com/x",
        page_text="text",
        page_title="title",
        category="cat",
        storage_score=0.5,
        kind="web",
        old_article_id="art1",
        service_id="example-com",
        og_image="",
    )

    assert "algorand quantum" in captured["enrichment_block"]
    assert "Algorand ships v5.0.0" in captured["enrichment_block"]


def test_recompose_via_writer_no_prior_findings_passes_empty_enrichment_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normal case (no prior search_x findings for this URL) passes an empty enrichment_block, not an error or a placeholder -- silent no-op, matching admin_sources' own "empty when no sources" contract."""
    from app.modules.newspaper.tasks import publish_tasks as pt

    monkeypatch.setattr(
        "app.modules.newspaper.admin_source_store.load_active_sources",
        lambda _article_id: [],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_prior_search_x_findings",
        lambda _service_id: [],
    )
    captured: dict = {}

    def _fake_compose(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    pt._recompose_via_writer(
        review_id="rev1",
        url="https://example.com/x",
        page_text="text",
        page_title="title",
        category="cat",
        storage_score=0.5,
        kind="web",
        old_article_id="art1",
        service_id="example-com",
        og_image="",
    )

    assert captured["enrichment_block"] == ""


def test_recompose_published_compose_reinjects_prior_search_x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive-refresh recompose path (_recompose_published_compose, the non-brief branch) also reinjects prior search_x findings, keyed on source_url -- the same value it passes to compose_scrape_article, matching what store_investigation_findings used as its service_id key on the article's prior composes."""
    from app.modules.newspaper.tasks import publish_tasks as pt

    monkeypatch.setattr(
        "app.modules.newspaper.admin_source_store.load_active_sources",
        lambda _article_id: [],
    )
    seen_service_ids: list[str] = []

    def _fake_load(service_id: str) -> list[dict]:
        seen_service_ids.append(service_id)
        return [
            {
                "query": "algorand quantum",
                "result": {"posts": [{"text": "hit", "likes": 1, "reposts": 0, "replies": 0}]},
            }
        ]

    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_prior_search_x_findings", _fake_load
    )
    captured: dict = {}

    def _fake_compose(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    class _FakeTask:
        def retry(self, *_a: object, **_kw: object) -> None:
            raise AssertionError("should not retry")

    pt._recompose_published_compose(
        _FakeTask(),
        article_id="art1",
        service_id="svc",
        source_url="https://example.com/x",
        page_text="text",
        page_title="title",
        brief_for_recompose=None,
    )

    assert seen_service_ids == ["https://example.com/x"]
    assert "algorand quantum" in captured["enrichment_block"]
