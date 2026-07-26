"""HTTP routes for treasury-payment-gated service suggestions."""

from __future__ import annotations

from robyn import Request, Response, Robyn

from app.core import serialization
from app.core.config import settings
from app.core.http_errors import json_error_from_platform, json_error_response
from app.modules.auth.services.auth_service import AuthService
from app.modules.auth.services.session_store import SessionStore
from app.modules.chain.repository import get_chain_repository
from app.modules.suggestions.models.domain import SuggestionError, UpvoteError
from app.modules.suggestions.models.schemas import (
    CreateSuggestionRequest,
    SuggestionConfigResponse,
    UpvoteRequest,
)
from app.modules.suggestions.services.suggestion_service import SuggestionService
from app.modules.suggestions.services.upvote_service import UpvoteService
from app.modules.suggestions.stores.factory import get_suggestion_store

# Every constructor here (auth/chain/suggestion/upvote) wraps a lazy
# store/client factory, so these are safe as module-level singletons shared
# by every route.
auth_service = AuthService(session_store=SessionStore())
suggestion_store = get_suggestion_store()
suggestion_service = SuggestionService(
    chain_repository=get_chain_repository(),
    store=suggestion_store,
    treasury_address=settings.platform_treasury_address,
    min_microalgos=settings.suggestion_min_microalgos,
)
upvote_service = UpvoteService(suggestion_store=suggestion_store)


def _session_wallet(request: Request) -> str | None:
    """The wallet address behind the request's session token, or None if absent/invalid."""
    token = request.headers.get("x-session-token") or ""
    if not token:
        return None
    session = auth_service.get_session(token)
    return session.wallet_address if session else None


def suggestions_config(request: Request) -> Response:
    """Treasury address and minimum payment required to submit a suggestion."""
    _ = request
    min_micro = settings.suggestion_min_microalgos
    algo_display = f"{min_micro / 1_000_000:.2f}".rstrip("0").rstrip(".")
    payload = SuggestionConfigResponse(
        treasury_address=settings.platform_treasury_address,
        min_microalgos=min_micro,
        min_algo_display=algo_display or "0",
    )
    return serialization.to_builtins(payload)


def create_suggestion(request: Request) -> Response:
    """Verify the treasury payment and record a new service suggestion."""
    wallet = _session_wallet(request)
    if not wallet:
        return json_error_response(401, "unauthorized", "Valid session required")

    try:
        payload = serialization.decode(request.body, CreateSuggestionRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        created = suggestion_service.create_suggestion(wallet, payload)
    except SuggestionError as exc:
        return json_error_from_platform(exc)

    return serialization.to_builtins(created)


def list_suggestions(request: Request) -> Response:
    """List open suggestions with their current upvote counts."""
    _ = request
    items = suggestion_service.list_open_suggestions()
    return {"items": serialization.to_builtins(items)}


def upvote_suggestion(request: Request) -> Response:
    """Verify a wallet's signature and record its upvote on a suggestion."""
    wallet = _session_wallet(request)
    if not wallet:
        return json_error_response(401, "unauthorized", "Valid session required")

    suggestion_id = request.path_params.get("suggestion_id", "")
    if not suggestion_id:
        return json_error_response(400, "invalid_request", "suggestion_id required")

    try:
        payload = serialization.decode(request.body, UpvoteRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        result = upvote_service.upvote(
            suggestion_id=suggestion_id,
            wallet_address=wallet,
            signature_b64=payload.signature_b64,
        )
    except UpvoteError as exc:
        return json_error_from_platform(exc)

    return result


def upvote_message(request: Request) -> Response:
    """The canonical message a wallet must sign to prove its upvote on a suggestion."""
    wallet = _session_wallet(request)
    if not wallet:
        return json_error_response(401, "unauthorized", "Valid session required")

    suggestion_id = request.path_params.get("suggestion_id", "")
    if not suggestion_id:
        return json_error_response(400, "invalid_request", "suggestion_id required")

    from app.modules.suggestions.services.upvote_message import build_upvote_signing_message

    message = build_upvote_signing_message(
        suggestion_id=suggestion_id,
        wallet_address=wallet,
    )
    return {"message": message, "suggestion_id": suggestion_id, "wallet_address": wallet}


def register_suggestions_routes(app: Robyn) -> None:
    """Register all service-suggestion and upvote API endpoints."""
    app.get("/api/v1/suggestions/config")(suggestions_config)
    app.post("/api/v1/suggestions")(create_suggestion)
    app.get("/api/v1/suggestions")(list_suggestions)
    app.post("/api/v1/suggestions/:suggestion_id/upvote")(upvote_suggestion)
    app.get("/api/v1/suggestions/:suggestion_id/upvote-message")(upvote_message)
