from __future__ import annotations

from app.core import serialization
from app.core.http_errors import json_error_response
from app.modules.search.services.search_service import SearchService
from app.modules.seo import analytics_store

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def register_search_routes(app) -> None:
    search_service = SearchService()

    @app.get("/api/v1/search")
    async def search(request):
        query = request.query_params.get("q", "").strip()
        if not query:
            return json_error_response(400, "invalid_request", "q query param required")
        limit_param = request.query_params.get("limit", str(_DEFAULT_LIMIT))
        service_id = request.query_params.get("service_id", None)
        limit = int(limit_param) if limit_param.isdigit() else _DEFAULT_LIMIT
        limit = min(max(1, limit), _MAX_LIMIT)
        result = search_service.search(query, limit=limit, service_id=service_id)
        # Record the term for editorial demand analytics (bots skipped inside).
        ua = request.headers.get("user-agent") or request.headers.get("User-Agent")
        analytics_store.record_search(query, len(result.items), user_agent=ua)
        return serialization.to_builtins(result)
