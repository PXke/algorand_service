"""HTTP routes for reader-facing article search."""

from __future__ import annotations

from typing import Any

from app.core import serialization
from app.core.http import Request, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.search.services.search_service import SearchService
from app.modules.seo import analytics_store

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def register_search_routes(app: Router) -> None:
    """Register the reader-facing article search route."""
    search_service = SearchService()

    @app.get("/api/v1/search")
    def search(request: Request) -> Any:  # noqa: ANN401 -- Robyn route handler returns a Response or any JSON-serializable builtin
        query = query_param(request.query_params.get("q", ""))
        if not query:
            return json_error_response(400, "invalid_request", "q query param required")
        limit_param = query_param(request.query_params.get("limit", str(_DEFAULT_LIMIT)))
        service_id = query_param(request.query_params.get("service_id", "")) or None
        lang = query_param(request.query_params.get("lang", "")) or None
        limit = int(limit_param) if limit_param.isdigit() else _DEFAULT_LIMIT
        limit = min(max(1, limit), _MAX_LIMIT)
        result = search_service.search(query, limit=limit, service_id=service_id, lang=lang)
        # Record the term for editorial demand analytics (bots skipped inside).
        ua = request.headers.get("user-agent") or request.headers.get("User-Agent")
        analytics_store.record_search(query, len(result.items), user_agent=ua)
        return serialization.to_builtins(result)
