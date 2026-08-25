"""HTTP routes for the reader-facing article feed and article detail."""

from __future__ import annotations

import hashlib
from email.utils import formatdate

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.core.tracking import tracking_opted_out_from_headers
from app.modules.news.services.news_service import NewsService

# The store factory behind this is lazy, so it's safe as a module-level
# singleton shared by every route.
news_service = NewsService()


def stats(request: Request) -> dict:
    """Article count and configured feed bucket."""
    _ = request
    return {
        "article_count": news_service.count_feed(),
        "feed_bucket": settings.news_feed_bucket,
    }


def tags(request: Request) -> dict:
    """Per-tag coverage/readership aggregate for the topics cloud.

    tag_stats() fans out a DISTINCT scan plus a COUNT and a LIST_RECENT per
    tag across the whole tag universe (see its own docstring) -- real work,
    not a cheap `len()`. At this site's actual (low) traffic a 300s TTL was
    expiring between real visits, so most readers hit the slow path anyway;
    the aggregate itself only moves when a new article is tagged, which at
    an hours-scale publish cadence is far slower than 300s. The cloud
    tolerates minutes-stale heat, so the TTL is set to tolerate them.
    """
    _ = request
    from app.core.cache import cached_json

    return cached_json("news:tags", 1800, news_service.tag_stats)


def hot(request: Request) -> dict:
    """Reader-engagement ranking (hot/top) over the recent feed, cached briefly."""
    limit_param = query_param(request.query_params.get("limit", ""))
    limit = min(int(limit_param), 50) if limit_param.isdigit() else 20
    lang = query_param(request.query_params.get("lang", "")) or None
    rank_param = query_param(request.query_params.get("rank", "")) or "hot"
    rank = rank_param if rank_param in ("hot", "top") else "hot"
    from app.core.cache import cached_json

    def compute() -> dict:
        items = news_service.hot_feed(limit=limit, lang=lang, rank=rank)
        return {"items": serialization.to_builtins(items)}

    return cached_json(f"news:hot:{rank}:{limit}:{lang or 'en'}", 300, compute)


def feed(request: Request) -> Response:
    """Keyset-paginated article feed, ETag/Last-Modified cacheable.

    Also cached server-side (Redis, cache-aside) behind the same `cached_json`
    primitive as /tags and /hot. Every filter param (service_id/tag/lang/limit)
    plus the cursor is folded into the key so pages and filtered views never
    collide. TTL is short (60s, vs /tags and /hot's 300s) because this is the
    write-sensitive default view readers land on; write paths additionally call
    `invalidate_feed_first_page()` to bust the no-cursor ("first page") entries
    immediately rather than waiting out the TTL -- see
    shared/algorand_shared/feed_cache.py for why only first pages are worth
    invalidating (older, cursor-paginated pages are immutable once cached).
    """
    limit_param = query_param(request.query_params.get("limit", ""))
    limit = int(limit_param) if limit_param.isdigit() else None
    service_id = query_param(request.query_params.get("service_id", "")) or None
    tag = query_param(request.query_params.get("tag", "")) or None
    cursor_param = query_param(request.query_params.get("cursor", ""))
    cursor = int(cursor_param) if cursor_param.isdigit() else None
    lang = query_param(request.query_params.get("lang", "")) or None

    from app.core.cache import cached_json

    # "first" vs "page" keeps the SCAN-based invalidation in feed_cache.py
    # (which only ever targets first pages) from having to touch cursor'd
    # pages at all -- those are stable until TTL regardless of new writes.
    cache_key = (
        f"news:feed:{'first' if cursor is None else 'page'}:"
        f"{service_id or '_'}:{tag or '_'}:{lang or '_'}:"
        f"{limit if limit is not None else '_'}:{cursor if cursor is not None else ''}"
    )

    def compute() -> dict:
        items, next_cursor = news_service.list_feed_page(
            limit=limit, service_id=service_id, tag=tag, cursor_epoch_ms=cursor, lang=lang
        )
        return {"items": serialization.to_builtins(items), "next_cursor": next_cursor}

    cached = cached_json(cache_key, 60, compute)
    items = cached["items"]
    next_cursor = cached["next_cursor"]

    body = serialization.encode(
        {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        }
    ).decode("utf-8")
    etag = f'"{hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]}"'
    headers = {
        "Content-Type": "application/json",
        "ETag": etag,
        "Cache-Control": "public, max-age=30",
    }
    newest_epoch = max((item.get("published_at_epoch") or 0 for item in items), default=0)
    if newest_epoch:
        headers["Last-Modified"] = formatdate(newest_epoch, usegmt=True)

    if_none_match = (
        request.headers.get("if-none-match") or request.headers.get("If-None-Match") or ""
    )
    if etag in if_none_match:
        return Response(status_code=304, headers=headers, description="")
    return Response(status_code=200, headers=headers, description=body)


def article_detail(request: Request) -> Response:
    """Fetch one article's full detail, recording a best-effort read (bots and opted-out readers excluded)."""
    raw = request.path_params.get("article_id", "")
    if not raw:
        return json_error_response(400, "invalid_request", "article_id required")

    # Accept a slug as well as a uuid. The SPA builds hrefs from article.slug
    # (migration 056), so after hydration EVERY article fetch arrives here as a
    # slug — resolving only in the SSR document route left the JSON API
    # answering 404 for every story, which the app renders as "this article was
    # deleted".
    article_id = news_service.resolve_slug(raw) or raw

    lang = query_param(request.query_params.get("lang", "")) or None

    # Count the read (best-effort). detail.views is the count before this
    # hit. Crawlers don't count: Googlebot's renderer boots the app and
    # fetches this JSON, which used to inflate "reads" — reuse the same UA
    # denylist the pageview analytics already trusts.
    from app.modules.news.stores.view_counts import record_view
    from app.modules.seo.analytics_store import (
        article_document_recently_served,
        is_bot,
        is_malformed_ua,
        is_repeated_ua,
    )

    should_count = False

    def _prep_view_gate() -> None:
        nonlocal should_count
        user_agent = request.headers.get("user-agent") or request.headers.get("User-Agent") or ""
        client_ip = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
        should_count = (
            not is_bot(user_agent)
            and not is_malformed_ua(user_agent)
            and not is_repeated_ua(user_agent)
            and not tracking_opted_out_from_headers(request.headers)
            # Second hand: this same (article, ip, ua) must have requested the
            # SSR document recently. Catches a scraper hitting this JSON
            # endpoint directly, which the UA-identity checks above can't (found
            # 2026-08-02: a UA/IP-rotating scraper walking ~88-90% of the
            # archive, none of it repeating enough to trip is_repeated_ua).
            and article_document_recently_served(article_id, client_ip, user_agent)
        )

    detail = news_service.get_article(article_id, lang=lang, overlap=_prep_view_gate)
    if detail is None:
        # 410 for a deliberately deleted article, 404 for one that never
        # existed — same split the HTML document route makes. The SPA needs
        # it to tell "removed" (render a tombstone page, and let crawlers see
        # the URL is permanently gone) from a plain bad id.
        from app.modules.news.stores.tombstones import is_article_tombstoned

        if is_article_tombstoned(article_id):
            return json_error_response(410, "gone", "Article removed")
        return json_error_response(404, "not_found", "Article not found")
    if should_count:
        record_view(article_id)
    return serialization.to_builtins(detail)


def register_news_routes(app: Router) -> None:
    """Register all reader-facing news feed and article API endpoints."""
    app.get("/api/v1/news/stats")(stats)
    app.get("/api/v1/news/tags")(tags)
    app.get("/api/v1/news/hot")(hot)
    app.get("/api/v1/news/feed")(feed)
    app.get("/api/v1/news/articles/:article_id")(article_detail)
