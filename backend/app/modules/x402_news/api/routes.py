"""HTTP routes for the x402 News Engine: free headlines, free article, paid search.

Route paths are /api/v1/x402/news/*: nginx only proxies `location ^~ /api/`
to this backend on the API host (deploy/nginx/algorand-platform.conf).
"""

from __future__ import annotations

import logging

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
from app.modules.x402_news.services.news_engine_service import NewsEngineService
from app.modules.x402_news.services.rate_limit import (
    news_article_rate_limited,
    news_list_rate_limited,
)

logger = logging.getLogger(__name__)

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


def x402_news_search(request: Request) -> Response:
    """Paid: ranked full-text search over every published article.

    `q` is validated (1-200 characters, non-blank) and `limit` parsed BEFORE
    the payment gate, so a malformed query is a 400 and never charged. A
    search whose engine failed outright (engine="error") is a 503 and is NOT
    marked fulfilled: the ledger row stays unfulfilled as the record that this
    payment bought nothing, for an operator to reconcile -- the failure is
    logged with the payment txid so that row can be found, and the payer
    still receives the settlement (receipt) headers on the 503.
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

    result = require_paid_request(
        request,
        price=settings.x402_news_search_price,
        resource=_SEARCH_RESOURCE,
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

    payload = news_engine.search(query, limit=limit)
    if payload["engine"] == "error":
        logger.error(
            "x402 news search: engine failed after payment; ledger row left unfulfilled "
            "(payment_txid=%s payer=%s q=%r)",
            result.payment_txid,
            result.payer,
            query,
        )
        response = json_error_response(
            503, "search_unavailable", "Search is temporarily unavailable — please retry"
        )
        response.headers.update(result.settlement_headers)
        return response
    mark_fulfilled(result.payment_txid, resource=_SEARCH_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**payload, "settlement_tx_id": result.payment_txid or ""}),
    )


def register_x402_news_routes(app: Router) -> None:
    """Register the two free News Engine routes and the one paid search route."""
    app.get("/api/v1/x402/news")(x402_news_list)
    app.get("/api/v1/x402/news/search")(x402_news_search)
    app.get("/api/v1/x402/news/articles/:article_id")(x402_news_article)
