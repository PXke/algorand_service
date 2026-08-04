"""HTTP routes for treasury-payment-gated service suggestions."""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.chain.repository import get_chain_repository
from app.modules.suggestions.services.suggestion_service import SuggestionService
from app.modules.suggestions.stores.factory import get_suggestion_store

# Every constructor here (chain/suggestion) wraps a lazy
# store/client factory, so these are safe as module-level singletons shared
# by every route.
suggestion_store = get_suggestion_store()
suggestion_service = SuggestionService(
    chain_repository=get_chain_repository(),
    store=suggestion_store,
    treasury_address=settings.platform_treasury_address,
    min_microalgos=settings.suggestion_min_microalgos,
)


def list_suggestions(request: Request) -> Response:
    """List open suggestions with their current upvote counts."""
    _ = request
    items = suggestion_service.list_open_suggestions()
    return {"items": serialization.to_builtins(items)}


def register_suggestions_routes(app: Router) -> None:
    """Register active service-suggestion API endpoints used by the UI."""
    app.get("/api/v1/suggestions")(list_suggestions)
