"""HTTP routes for the x402 News Engine: free headlines, tags and article, paid search.

Route paths are /api/v1/x402/news/*: nginx only proxies `location ^~ /api/`
to this backend on the API host (deploy/nginx/algorand-platform.conf).

The free reads (headline list, tag discovery, article) all accept the same
optional `lang` the public site serves: the newspaper's translation pipeline
stores full per-language article translations, and an agent consuming the
feed in one of those languages should not have to scrape the human site to
get them.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import promo_request_params
from app.modules.x402_news.services.news_engine_service import NewsEngineService
from app.modules.x402_news.services.rate_limit import (
    news_article_rate_limited,
    news_list_rate_limited,
    news_tags_rate_limited,
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


def _parse_limit(
    request: Request, *, clamp: Callable[[int | None], int] | None = None
) -> int | Response:
    """The caller's `limit`, clamped to the configured maximum; a 400 Response when non-integer.

    `clamp` defaults to the headline/search cap (news_engine.clamp_limit);
    the tag list passes its own (clamp_tag_limit). Resolved at call time, not
    as a default argument, because tests rebind the module-level engine.
    """
    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        requested = int(raw_limit) if raw_limit else None
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")
    return (clamp or news_engine.clamp_limit)(requested)


def _parse_search_input(request: Request) -> tuple[str, int] | Response:
    """The search route's validated (q, limit), or the 400 Response to return.

    q must be _MIN_QUERY_LENGTH-_MAX_QUERY_LENGTH characters; limit is parsed
    and clamped by _parse_limit. Extracted from x402_news_search so the
    route's own control flow stays within the complexity budget once the
    unpaid-challenge branch was added ahead of this validation.
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
    return query, limit


# The translation pipeline stores plain lowercase two-letter codes (ps, ar,
# fa, ru, zh, hi, es, fr today); the optional region suffix keeps a
# BCP-47-shaped request like fr-ca from being a 400 (it just won't match a
# stored translation and reads as English).
_LANG_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


def _parse_lang(request: Request) -> str | None | Response:
    """The caller's `lang`, normalized lowercase; None when absent, a 400 Response when malformed.

    Only the SHAPE is validated here -- a well-formed code with no stored
    translation is not an error, it serves English (the article payload's
    `lang` field says which was actually served).
    """
    raw = query_param(request.query_params.get("lang", "")).strip().lower()
    if not raw:
        return None
    if not _LANG_PATTERN.match(raw):
        return json_error_response(
            400, "invalid_request", "lang must be a two-letter language code (e.g. fr, es, ru)"
        )
    return raw


def x402_news_list(request: Request) -> Response | dict:
    """Free: the latest published headlines, newest first, rate-limited per IP.

    Optional `tag` narrows to one topic (GET /x402/news/tags lists the live
    tag universe), optional `limit` is clamped to x402_news_max_results, and
    optional `lang` overlays each headline's title/summary with that stored
    translation where one exists (best-effort per item, English otherwise).
    Each item carries the id and slug the free article route accepts, plus
    the public site URL.
    """
    if news_list_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many news requests — please try again later"
        )

    limit = _parse_limit(request)
    if isinstance(limit, Response):
        return limit
    lang = _parse_lang(request)
    if isinstance(lang, Response):
        return lang
    tag = query_param(request.query_params.get("tag", "")) or None

    return {"items": news_engine.list_headlines(limit=limit, tag=tag, lang=lang)}


def x402_news_tags(request: Request) -> Response | dict:
    """Free: the newspaper's live tag taxonomy, rate-limited per IP.

    Serves per-tag article count, total readership and last-published epoch,
    sorted by coverage -- the discovery surface for the `tag` filter the
    headline list accepts (without this, an agent could filter by tag but
    had no way to learn which tags exist short of scraping the human site).
    Optional `limit` is clamped to x402_news_max_tags; `tag_count_total`
    says how many tags exist beyond the served slice.
    """
    if news_tags_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many tag requests — please try again later"
        )

    limit = _parse_limit(request, clamp=news_engine.clamp_tag_limit)
    if isinstance(limit, Response):
        return limit

    return news_engine.tag_stats(limit=limit)


def x402_news_article(request: Request) -> Response:
    """Free: one published article in full -- body markdown, sources, translations.

    Rate-limited per IP (its own counter, same budget as the headline list):
    the article content is already free on the public website, so charging
    agents again for the same read would be redundant -- the paid search
    route is the marketplace's actual differentiated capability here.

    Optional `lang` serves the stored translation of the body/title/summary
    where one exists (`translations_available` in any article payload lists
    the codes); the payload's `lang` field always says which language was
    actually served, so a missing translation reads as an explicit `"en"`,
    never a silent surprise.

    The translations list is a nice-to-have: if its lookup fails the article
    is still served, with an empty list (and `lang: "en"`).
    """
    raw = request.path_params.get("article_id", "")
    if not raw:
        return json_error_response(400, "invalid_request", "article_id required")

    if news_article_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many article requests — please try again later"
        )

    lang = _parse_lang(request)
    if isinstance(lang, Response):
        return lang

    detail = news_engine.resolve_article(raw, lang=lang)
    if detail is None:
        return json_error_response(
            404,
            "not_found",
            "No published article with that id or slug. GET /api/v1/x402/news lists "
            "the latest headlines, free of charge.",
        )

    payload = news_engine.article_json(detail, lang_requested=lang)
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


def _search_preview_response(query: str) -> Response:
    """The redacted `?preview=true` response for the paid search: shape, not values.

    The search engine is never called for a preview caller -- even "how
    many hits does this query have" is part of what the route sells, so
    nothing derived from the real index may leak. One redacted exemplar row
    with the same keys as _SEARCH_OUTPUT_EXAMPLE's and a sentinel score
    (-1.0 -- a real relevance score is never negative).
    """
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {
                "query": query,
                "engine": "<preview>",
                "items": [
                    {
                        "article_id": "<preview>",
                        "slug": "<preview>",
                        "title": "<preview>",
                        "summary": "<preview>",
                        "snippet": "<preview>",
                        "score": -1.0,
                        "published_at_epoch": 0,
                        "url": "<preview>",
                    }
                ],
                "settlement_tx_id": "<preview>",
            }
        ),
    )


def _search_promo_response(result: PaymentResult, query: str, *, limit: int) -> Response:
    """The real search for a promo bypass: no settlement, so no refund path, but no fabricated 200 either.

    A promo bypass still must not pass off an engine failure as a 200, same
    as the real-payment path -- but run_with_refund is not used here: that
    mechanism is specifically for undoing a real settlement, and a promo
    redemption settled nothing.
    """
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

    Supports `?preview=true` (modules/x402/preview.py): the response SHAPE
    with every value redacted, unpaid and preview-rate-limited -- the search
    engine itself is never run for a preview caller, so not even a hit count
    leaks for free.
    """
    if circuit_breaker.is_tripped(_SEARCH_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    offer = {
        "price": settings.x402_news_search_price,
        "resource": _SEARCH_RESOURCE,
        "description": (
            "Full-text search over every published PXke Algorand newspaper article "
            "(Typesense-ranked, typo-tolerant, synonym-aware). Returns up to "
            f"{settings.x402_news_max_results} ranked hits with title, summary, a "
            "highlighted snippet, the public site URL, and the id/slug to pass to "
            "the free article route for the full body. Supports ?preview=true "
            "(redacted, unpaid, rate-limited)."
        ),
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its query string is validated
    # (see challenge_if_unpaid); with a payment attached, q and limit are
    # still validated before the gate so a malformed query is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    parsed = _parse_search_input(request)
    if isinstance(parsed, Response):
        return parsed
    query, limit = parsed

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        return _search_preview_response(query)

    if result.is_promo:
        return _search_promo_response(result, query, limit=limit)

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
    """Register the three free News Engine routes and the one paid search route."""
    app.get("/api/v1/x402/news")(x402_news_list)
    app.get("/api/v1/x402/news/tags")(x402_news_tags)
    app.get("/api/v1/x402/news/search")(x402_news_search)
    app.get("/api/v1/x402/news/articles/:article_id")(x402_news_article)
