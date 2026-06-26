from __future__ import annotations

import hashlib
import json
from email.utils import formatdate

from robyn import Request, Response

from app.core.config import settings
from app.core.http_errors import json_error_response
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

    @app.get("/api/v1/news/feed")
    async def feed(request: Request) -> Response:
        limit_param = request.query_params.get("limit", "")
        limit = int(limit_param) if limit_param.isdigit() else None
        service_id = request.query_params.get("service_id", "") or None
        cursor_param = request.query_params.get("cursor", "")
        cursor = int(cursor_param) if cursor_param.isdigit() else None
        items, next_cursor = news_service.list_feed_page(
            limit=limit, service_id=service_id, cursor_epoch_ms=cursor
        )

        body = json.dumps(
            {
                "items": [item.model_dump() for item in items],
                "next_cursor": next_cursor,
                "has_more": next_cursor is not None,
            },
            separators=(",", ":"),
        )
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
        detail = news_service.get_article(article_id)
        if detail is None:
            return json_error_response(404, "not_found", "Article not found")
        # Count the read (best-effort). detail.views is the count before this hit.
        from app.modules.news.stores.view_counts import record_view

        record_view(article_id)
        return detail.model_dump()
