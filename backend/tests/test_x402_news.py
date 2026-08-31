"""x402 News Engine tests: free headline list, free article read, paid search.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_board.py's), Redis is a fake at the get_redis seam, the
articles live in the news module's own in-memory store, and Typesense is
stubbed out so search falls through to the feed-scan path. Nothing here
settles a real payment or reaches TestNet.

Replay protection and the settlement ledger are shared infrastructure
(modules/x402/) already covered by test_x402_directory.py -- they are not
re-tested here. What IS News-Engine-specific and tested here: the pre-gate
404 for unknown/draft articles, the pre-gate query validation for search, the
paid search payload, the free list's and free article's bounds and rate
limits, and the two paid-but-degraded search paths (translations lookup
failing after a free article read, search engine failing after payment).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from falcon import testing
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore
from app.modules.search.services import search_service as search_service_module
from app.modules.search.services.search_service import SearchService
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import replay as replay_module
from app.modules.x402_news.api import routes as news_routes
from app.modules.x402_news.services.news_engine_service import NewsEngineService

_PAY_TO = "A" * 58
_PAYER = "P" * 58

_LIVE_ID = "11111111-1111-4111-8111-111111111111"
_DRAFT_ID = "22222222-2222-4222-8222-222222222222"
_UNKNOWN_ID = "33333333-3333-4333-8333-333333333333"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the replay claim and the rate-limit counter."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


class _StubFacilitator:
    """Canned /supported. verify()/settle() raise: no test here settles a payment through the gate."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called without a payment header")

    def settle(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called without a payment header")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


def _request(
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
    path: str = "/api/v1/x402/news",
) -> Request:
    return Request(
        method="GET",
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
        body=b"",
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


def _settled_result(payer: str = _PAYER, txid: str = "TX123") -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="10000",
        payment_txid=txid,
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )


def _article(
    article_id: str,
    *,
    title: str,
    slug: str,
    published_at_epoch: int,
    tags: list[str] | None = None,
    draft: bool = False,
    translations: dict[str, str] | None = None,
) -> StoredArticle:
    return StoredArticle(
        article_id=article_id,
        service_id="tinyman",
        title=title,
        summary=f"Summary of {title}",
        body=f"## Body\n\nFull markdown body of {title}.",
        published_at_epoch=published_at_epoch,
        source_url="https://source.example/post",
        tags=tags or ["defi"],
        slug=slug,
        translations=translations,
        draft=draft,
    )


def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
    raise AssertionError("the payment gate must not run for a request that was already doomed")


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> NewsEngineService:
    """A News Engine over a seeded in-memory article store, Typesense stubbed out, wired into the routes."""
    store = InMemoryArticleStore()
    store.insert(
        _article(
            _LIVE_ID,
            title="Tinyman v2 crosses one billion",
            slug="tinyman-v2-crosses-one-billion",
            published_at_epoch=1_756_377_600,
            tags=["defi", "tinyman"],
            translations={
                "fr": json.dumps({"title": "Tinyman v2 dépasse un milliard"}),
                "de": json.dumps({"title": "Tinyman v2 überschreitet eine Milliarde"}),
            },
        )
    )
    store.insert(
        _article(
            "44444444-4444-4444-8444-444444444444",
            title="Governance period twelve opens",
            slug="governance-period-twelve-opens",
            published_at_epoch=1_756_291_200,
            tags=["governance"],
        )
    )
    draft = _article(
        _DRAFT_ID,
        title="Unpublished scoop",
        slug="unpublished-scoop",
        published_at_epoch=1_756_464_000,
        draft=True,
    )
    store.insert(draft)
    # Mirror prod: a draft is removed from the articles_feed projection but
    # stays readable by id/slug (StoredArticle.draft's own comment), so only
    # the direct lookup can ever see it -- the memory store's insert() has no
    # draft-aware feed, hence the manual removal.
    store._feed.remove(draft)
    news_service = NewsService(store)
    # Typesense is "configured" by default settings; stub it out so search
    # takes the feed-scan path over the in-memory store instead of a socket.
    monkeypatch.setattr(search_service_module, "get_typesense_client", lambda: None)
    service = NewsEngineService(news_service, SearchService(news_service))
    monkeypatch.setattr(news_routes, "news_engine", service)
    return service


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet and the offline stub facilitator."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap both Redis seams for one in-process fake shared by replay and rate limiting."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    return client


@pytest.fixture
def fulfilled(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str]]:
    """Capture every mark_fulfilled call the routes make as (txid, resource)."""
    calls: list[tuple[str | None, str]] = []
    monkeypatch.setattr(
        news_routes,
        "mark_fulfilled",
        lambda txid, *, resource: calls.append((txid, resource)) or True,
    )
    return calls


# --------------------------------------------------------------------------- #
# The free headline list
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("engine", "fake_redis")
def test_headline_list_is_free_newest_first_with_public_urls() -> None:
    """The free list returns published headlines newest first, never a draft, each with the id, slug and public site URL."""
    result = news_routes.x402_news_list(_request())

    titles = [item["title"] for item in result["items"]]
    assert titles == ["Tinyman v2 crosses one billion", "Governance period twelve opens"]
    first = result["items"][0]
    assert first["article_id"] == _LIVE_ID
    assert first["slug"] == "tinyman-v2-crosses-one-billion"
    assert first["url"].endswith("/news/articles/tinyman-v2-crosses-one-billion")
    assert first["tags"] == ["defi", "tinyman"]
    assert "body_markdown" not in first


@pytest.mark.usefixtures("engine", "fake_redis")
def test_headline_list_filters_by_tag() -> None:
    """`tag` narrows the free list to one topic."""
    result = news_routes.x402_news_list(_request(query={"tag": "governance"}))

    assert [item["slug"] for item in result["items"]] == ["governance-period-twelve-opens"]


@pytest.mark.usefixtures("engine", "fake_redis")
def test_headline_list_limit_is_clamped_to_the_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller cannot ask for an unbounded listing -- the limit is clamped."""
    monkeypatch.setattr(settings, "x402_news_max_results", 1)

    result = news_routes.x402_news_list(_request(query={"limit": "9999"}))

    assert len(result["items"]) == 1


@pytest.mark.usefixtures("engine", "fake_redis")
def test_headline_list_rejects_a_non_integer_limit() -> None:
    """A non-integer limit is a 400 rather than silently ignored."""
    result = news_routes.x402_news_list(_request(query={"limit": "lots"}))

    assert result.status_code == 400
    assert "invalid_request" in result.description


@pytest.mark.usefixtures("engine", "fake_redis")
def test_headline_list_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """An IP over the hourly budget gets a 429; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_news_rate_limit_per_hour", 2)

    def _read(ip: str) -> Response | dict:
        return news_routes.x402_news_list(_request(headers={"X-Real-IP": ip}))

    assert "items" in _read("203.0.113.7")
    assert "items" in _read("203.0.113.7")
    limited = _read("203.0.113.7")
    assert limited.status_code == 429
    assert "items" in _read("203.0.113.9")


# --------------------------------------------------------------------------- #
# The free article read
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("engine", "fake_redis")
@pytest.mark.parametrize("raw_id", [_UNKNOWN_ID, "no-such-slug", _DRAFT_ID, "unpublished-scoop"])
def test_an_unknown_or_unpublished_article_is_a_404(raw_id: str) -> None:
    """An unknown id/slug, or a draft's, is a 404 -- nothing to serve."""
    response = news_routes.x402_news_article(_request(path_params={"article_id": raw_id}))

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("engine", "fake_redis")
def test_a_published_article_is_served_free_with_no_payment_required() -> None:
    """A published article comes back as a plain 200, full body, sources, translations and public URL, no payment gate involved."""
    response = news_routes.x402_news_article(
        _request(path_params={"article_id": "tinyman-v2-crosses-one-billion"})
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["article_id"] == _LIVE_ID
    assert body["body_markdown"].startswith("## Body")
    assert body["sources"] == ["https://source.example/post"]
    assert body["translations_available"] == ["de", "fr"]
    assert body["url"].endswith("/news/articles/tinyman-v2-crosses-one-billion")


@pytest.mark.usefixtures("fake_redis")
def test_a_failed_translations_lookup_still_serves_the_free_article(
    engine: NewsEngineService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The translations list is a nice-to-have read: when it raises the article body is still served, with an empty list, and the failure is logged."""

    def _boom(_article_id: str) -> Never:
        raise RuntimeError("cassandra read timed out")

    monkeypatch.setattr(engine._news, "translation_langs_for", _boom)

    with caplog.at_level(logging.WARNING):
        response = news_routes.x402_news_article(_request(path_params={"article_id": _LIVE_ID}))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["body_markdown"].startswith("## Body")
    assert body["translations_available"] == []
    assert any("translation lookup failed" in rec.message for rec in caplog.records)


@pytest.mark.usefixtures("engine", "fake_redis")
def test_the_article_route_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free article read is capped per IP on its own counter: over budget is a 429 before the store is read; the headline list's counter is separate."""
    monkeypatch.setattr(settings, "x402_news_rate_limit_per_hour", 2)

    def _read(ip: str) -> Response:
        return news_routes.x402_news_article(
            _request(headers={"X-Real-IP": ip}, path_params={"article_id": _LIVE_ID})
        )

    assert _read("203.0.113.7").status_code == 200
    assert _read("203.0.113.7").status_code == 200
    # A different IP is unaffected, and so is the limited IP's free headline
    # list (its own counter).
    assert _read("203.0.113.9").status_code == 200
    assert "items" in news_routes.x402_news_list(_request(headers={"X-Real-IP": "203.0.113.7"}))

    monkeypatch.setattr(news_routes.news_engine, "resolve_article", _must_not_charge)
    limited = _read("203.0.113.7")
    assert limited.status_code == 429
    assert "rate_limited" in limited.description


# --------------------------------------------------------------------------- #
# The paid search
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("engine", "fake_redis")
@pytest.mark.parametrize("bad_q", ["", "   ", "z" * 201])
def test_an_invalid_query_is_a_400_before_the_gate(
    bad_q: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank or over-long q is a 400 taken BEFORE the gate -- nobody is charged to submit an unusable query."""
    monkeypatch.setattr(news_routes, "require_paid_request", _must_not_charge)

    response = news_routes.x402_news_search(_request(query={"q": bad_q}))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("engine", "testnet_settings", "fake_redis")
def test_search_without_payment_returns_402_with_the_search_price() -> None:
    """A valid query without a payment header yields a 402 at the search price, with a query-params discovery extension."""
    from x402.http.utils import decode_payment_required_header

    response = news_routes.x402_news_search(_request(query={"q": "tinyman"}))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    # $0.001 USDC in atomic units at 6 decimals.
    assert payment_required.accepts[0].amount == "1000"
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    assert "search" in (payment_required.resource.description or "").lower()


@pytest.mark.usefixtures("engine", "fake_redis")
def test_a_settled_search_returns_ranked_hits_and_marks_it_fulfilled(
    monkeypatch: pytest.MonkeyPatch, fulfilled: list[tuple[str | None, str]]
) -> None:
    """Once payment settles the search runs through the search module and the hits come back with public URLs."""
    monkeypatch.setattr(news_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())

    response = news_routes.x402_news_search(_request(query={"q": "governance"}))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["query"] == "governance"
    assert [hit["article_id"] for hit in body["items"]] == ["44444444-4444-4444-8444-444444444444"]
    # The feed-scan fallback (what runs with Typesense stubbed out) carries no
    # slug, so the URL falls back to the id form -- which the site 301s.
    assert body["items"][0]["url"].endswith("/news/articles/44444444-4444-4444-8444-444444444444")
    assert body["settlement_tx_id"] == "TX123"
    assert fulfilled == [("TX123", "x402-news-search")]


@pytest.mark.usefixtures("fake_redis")
def test_a_search_whose_engine_failed_is_a_503_and_not_marked_fulfilled(
    engine: NewsEngineService,
    monkeypatch: pytest.MonkeyPatch,
    fulfilled: list[tuple[str | None, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An engine failure after payment is a 503 with the ledger row left unfulfilled -- an empty result is never passed off as "no matches". The payer still gets the settlement headers (the receipt) and the failure is logged with the payment txid so the operator can find the row."""
    monkeypatch.setattr(news_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(
        engine,
        "search",
        lambda query, *, limit: {"query": query, "engine": "error", "items": [], "limit": limit},
    )

    with caplog.at_level(logging.ERROR):
        response = news_routes.x402_news_search(_request(query={"q": "governance"}))

    assert response.status_code == 503
    assert "search_unavailable" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    assert response.headers["Content-Type"] == "application/json"
    assert fulfilled == []
    errors = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert any("TX123" in rec.getMessage() for rec in errors)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def test_news_engine_is_registered_only_when_the_news_store_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The News Engine is gated on the NEWS store's setting (it has none of its own): memory leaves it unregistered, cassandra registers it."""
    from app.falcon_main import create_app
    from app.falcon_main import settings as falcon_main_settings

    monkeypatch.setattr(falcon_main_settings, "x402_enabled", True)
    monkeypatch.setattr(falcon_main_settings, "news_store", "memory")
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402/news").status_code == 404

    monkeypatch.setattr(falcon_main_settings, "news_store", "cassandra")
    # The route serves off the already-built (memory) store singleton here;
    # only registration is under test, not a Cassandra read.
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402/news").status_code != 404
