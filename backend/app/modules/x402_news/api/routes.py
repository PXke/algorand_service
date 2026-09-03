"""HTTP routes for the x402 News Engine: free headlines, free article, paid search.

Route paths are /api/v1/x402/news/*: nginx only proxies `location ^~ /api/`
to this backend on the API host (deploy/nginx/algorand-platform.conf).
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402.promo import promo_request_params
from app.modules.x402_news.services.news_engine_service import NewsEngineService
from app.modules.x402_news.services.rate_limit import (
    news_article_rate_limited,
    news_list_rate_limited,
)

# The stores behind the news/search services are resolved lazily on first
# use, so this is safe as a module-level singleton shared by all routes.
news_engine = NewsEngineService()

_SEARCH_RESOURCE = "x402-news-search"

_MIN_QUERY_LENGTH = 1
_MAX_QUERY_LENGTH = 200

_EXAMPLE_ID = "6f1c2a4e-3b5d-4c7e-9a1b-2d3e4f5a6b7c"
_EXAMPLE_SLUG = "tinyman-v2-crosses-1b-cumulative-volume"
_EXAMPLE_URL = f"https://algorand.pxke.me/news/articles/{_EXAMPLE_SLUG}"

_SEARCH_OUTPUT_EXAMPLE = {
    "query": "tinyman volume",
    "engine": "typesense",
    "items": [
        {
            "article_id": _EXAMPLE_ID,
            "slug": _EXAMPLE_SLUG,
            "title": "Tinyman v2 crosses $1B cumulative volume",
            "summary": "The Algorand DEX passed the milestone on 2026-08-28.",
            "snippet": "... cumulative <mark>volume</mark> on <mark>Tinyman</mark> v2 ...",
            "score": 1157451471441100800.0,
            "published_at_epoch": 1756377600,
            "url": _EXAMPLE_URL,
        }
    ],
    "settlement_tx_id": "...",
}


def _parse_limit(request: Request) -> int | Response:
    """The caller's `limit`, clamped to the configured maximum; a 400 Response when non-integer."""
    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        requested = int(raw_limit) if raw_limit else None
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")
    return news_engine.clamp_limit(requested)


def x402_news_list(request: Request) -> Response | dict:
    """Free: the latest published headlines, newest first, rate-limited per IP.

    Optional `tag` narrows to one topic, optional `limit` is clamped to
    x402_news_max_results. Each item carries the id and slug the free article
    route accepts, plus the public site URL.
    """
    if news_list_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many news requests — please try again later"
        )

    limit = _parse_limit(request)
    if isinstance(limit, Response):
        return limit
    tag = query_param(request.query_params.get("tag", "")) or None

    return {"items": news_engine.list_headlines(limit=limit, tag=tag)}


def x402_news_article(request: Request) -> Response:
    """Free: one published article in full -- body markdown, sources, translations.

    Rate-limited per IP (its own counter, same budget as the headline list):
    the article content is already free on the public website, so charging
    agents again for the same read would be redundant -- the paid search
    route is the marketplace's actual differentiated capability here.

    The translations list is a nice-to-have: if its lookup fails the article
    is still served, with an empty list.
    """
    raw = request.path_params.get("article_id", "")
    if not raw:
        return json_error_response(400, "invalid_request", "article_id required")

    if news_article_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many article requests — please try again later"
        )

    detail = news_engine.resolve_article(raw)
    if detail is None:
        return json_error_response(
            404,
            "not_found",
            "No published article with that id or slug. GET /api/v1/x402/news lists "
            "the latest headlines, free of charge.",
        )

    payload = news_engine.article_json(detail)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(payload),
    )


class _SearchEngineFailed(Exception):
    """Raised by `_news_search_product_write` when the search engine itself failed.

    Lets `run_with_refund` treat "the engine failed after payment" the same
    as any other product-write failure -- refund the payer instead of
    leaving the ledger row unfulfilled for manual reconciliation (the old
    behaviour, from before the refund mechanism existed).
    """


def x402_news_search(request: Request) -> Response:
    """Paid: ranked full-text search over every published article.

    `q` is validated (1-200 characters, non-blank) and `limit` parsed BEFORE
    the payment gate, so a malformed query is a 400 and never charged.
    `circuit_breaker.is_tripped` is checked before the gate too, so a
    resource with too many recent refund-triggering failures is refused
    before anyone is charged again (modules/x402/circuit_breaker.py).

    A search whose engine failed outright (engine="error") now goes through
    `run_with_refund` (modules/x402/paid_request.py) like any other
    product-write failure: the payer is refunded from the dedicated refund
    wallet instead of the payment sitting as an unfulfilled ledger row for
    an operator to notice by hand.
    """
    query = query_param(request.query_params.get("q", ""))
    if not (_MIN_QUERY_LENGTH <= len(query) <= _MAX_QUERY_LENGTH):
        return json_error_response(
            400,
            "invalid_request",
            f"q must be {_MIN_QUERY_LENGTH}-{_MAX_QUERY_LENGTH} characters",
        )
    limit = _parse_limit(request)
    if isinstance(limit, Response):
        return limit

    if circuit_breaker.is_tripped(_SEARCH_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_news_search_price,
        resource=_SEARCH_RESOURCE,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        description=(
            "Full-text search over every published PXke Algorand newspaper article "
            "(Typesense-ranked, typo-tolerant, synonym-aware). Returns up to "
            f"{settings.x402_news_max_results} ranked hits with title, summary, a "
            "highlighted snippet, the public site URL, and the id/slug to pass to "
            "the free article route for the full body."
        ),
        extensions=describe_json_endpoint(
            input={"q": "tinyman volume", "limit": 10},
            input_schema={
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "minLength": _MIN_QUERY_LENGTH,
                        "maxLength": _MAX_QUERY_LENGTH,
                    },
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["q"],
            },
            output_example=_SEARCH_OUTPUT_EXAMPLE,
        ),
    )
    if result.error:
        return result.error

    if result.is_promo:
        # Nothing settled, so nothing to refund -- but a promo bypass still
        # must not fabricate a 200 when the engine genuinely failed, same as
        # the real-payment path. No run_with_refund here: that mechanism is
        # specifically for undoing a real settlement.
        payload = news_engine.search(query, limit=limit)
        if payload["engine"] == "error":
            response = json_error_response(
                503, "search_unavailable", "Search is temporarily unavailable — please retry"
            )
            response.headers.update(result.settlement_headers)
            return response
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps({**payload, "settlement_tx_id": "", "via": "promo"}),
        )

    def _news_search_product_write() -> dict:
        payload = news_engine.search(query, limit=limit)
        if payload["engine"] == "error":
            raise _SearchEngineFailed(f"search engine failed for q={query!r}")
        return payload

    outcome = run_with_refund(
        result, resource=_SEARCH_RESOURCE, product_write=_news_search_product_write, request=request
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_SEARCH_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def register_x402_news_routes(app: Router) -> None:
    """Register the two free News Engine routes and the one paid search route."""
    app.get("/api/v1/x402/news")(x402_news_list)
    app.get("/api/v1/x402/news/search")(x402_news_search)
    app.get("/api/v1/x402/news/articles/:article_id")(x402_news_article)
