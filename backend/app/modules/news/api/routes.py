from __future__ import annotations

import hashlib
from email.utils import formatdate

from robyn import Request, Response

from app.core import serialization
from app.core.config import settings
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.core.tracking import tracking_opted_out_from_headers
from app.modules.news.services.news_service import NewsService


def register_news_routes(app) -> None:
    news_service = NewsService()

    @app.get("/api/v1/news/stats")
    async def stats(request: Request) -> dict:
        _ = request
        return {
            "article_count": news_service.count_feed(),
            "feed_bucket": settings.news_feed_bucket,
        }

    @app.get("/api/v1/news/tags")
    async def tags(request: Request) -> dict:
        """Per-tag coverage/readership aggregate for the topics cloud. The scan
        joins view counters across the recent feed, so it hides behind a short
        cache; the cloud tolerates minutes-stale heat."""
        _ = request
        from app.core.cache import cached_json

        return cached_json("news:tags", 300, news_service.tag_stats)

    @app.get("/api/v1/news/hot")
    async def hot(request: Request) -> dict:
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

    @app.get("/api/v1/news/feed")
    async def feed(request: Request) -> Response:
        limit_param = query_param(request.query_params.get("limit", ""))
        limit = int(limit_param) if limit_param.isdigit() else None
        service_id = query_param(request.query_params.get("service_id", "")) or None
        tag = query_param(request.query_params.get("tag", "")) or None
        cursor_param = query_param(request.query_params.get("cursor", ""))
        cursor = int(cursor_param) if cursor_param.isdigit() else None
        lang = query_param(request.query_params.get("lang", "")) or None
        items, next_cursor = news_service.list_feed_page(
            limit=limit, service_id=service_id, tag=tag, cursor_epoch_ms=cursor, lang=lang
        )

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
        newest_epoch = max((item.published_at_epoch for item in items), default=0)
        if newest_epoch:
            headers["Last-Modified"] = formatdate(newest_epoch, usegmt=True)

        if_none_match = (
            request.headers.get("if-none-match") or request.headers.get("If-None-Match") or ""
        )
        if etag in if_none_match:
            return Response(status_code=304, headers=headers, description="")
        return Response(status_code=200, headers=headers, description=body)

    @app.get("/api/v1/news/articles/:article_id")
    async def article_detail(request: Request) -> Response:
        article_id = request.path_params.get("article_id", "")
        if not article_id:
            return json_error_response(400, "invalid_request", "article_id required")
            
        lang = query_param(request.query_params.get("lang", "")) or None
        detail = news_service.get_article(article_id, lang=lang)
        if detail is None:
            return json_error_response(404, "not_found", "Article not found")
        # Count the read (best-effort). detail.views is the count before this
        # hit. Crawlers don't count: Googlebot's renderer boots the app and
        # fetches this JSON, which used to inflate "reads" — reuse the same UA
        # denylist the pageview analytics already trusts.
        from app.modules.news.stores.view_counts import record_view
        from app.modules.seo.analytics_store import is_bot, is_malformed_ua, is_repeated_ua

        user_agent = (
            request.headers.get("user-agent") or request.headers.get("User-Agent") or ""
        )
        if (
            not is_bot(user_agent)
            and not is_malformed_ua(user_agent)
            and not is_repeated_ua(user_agent)
            and not tracking_opted_out_from_headers(request.headers)
        ):
            record_view(article_id)
        return serialization.to_builtins(detail)
