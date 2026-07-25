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


def register_suggestions_routes(app: Robyn) -> None:
    """Register all service-suggestion and upvote API endpoints."""
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
        token = request.headers.get("x-session-token") or ""
        if not token:
            return None
        session = auth_service.get_session(token)
        return session.wallet_address if session else None

    @app.get("/api/v1/suggestions/config")
    async def suggestions_config(request: Request) -> Response:
        _ = request
        min_micro = settings.suggestion_min_microalgos
        algo_display = f"{min_micro / 1_000_000:.2f}".rstrip("0").rstrip(".")
        payload = SuggestionConfigResponse(
            treasury_address=settings.platform_treasury_address,
            min_microalgos=min_micro,
            min_algo_display=algo_display or "0",
        )
        return serialization.to_builtins(payload)

    @app.post("/api/v1/suggestions")
    async def create_suggestion(request: Request) -> Response:
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

    @app.get("/api/v1/suggestions")
    async def list_suggestions(request: Request) -> Response:
        _ = request
        items = suggestion_service.list_open_suggestions()
        return {"items": serialization.to_builtins(items)}

    @app.post("/api/v1/suggestions/:suggestion_id/upvote")
    async def upvote_suggestion(request: Request) -> Response:
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

    @app.get("/api/v1/suggestions/:suggestion_id/upvote-message")
    async def upvote_message(request: Request) -> Response:
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
