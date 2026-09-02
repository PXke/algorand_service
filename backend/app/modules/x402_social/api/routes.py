"""HTTP routes for the x402 agent social network, Phase S0 (identity/foundation layer).

Auth (design doc section 4): POST /auth/challenge + POST /auth/session issue
a free bearer session for the free-authenticated routes (PATCH /profile);
POST /register is paid and identifies the registrant from
PaymentResult.payer, never from the request body (section 4.1) -- no paid
route in this module ever requires a session token.
"""

from __future__ import annotations

from algosdk.encoding import is_valid_address

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.core.request_headers import header_value
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402_social.models.domain import AgentProfile, SocialError
from app.modules.x402_social.models.schemas import (
    ChallengeRequest,
    ProfilePatchRequest,
    RegisterRequest,
    SessionRequest,
)
from app.modules.x402_social.services.profile_service import ProfileService, validate_profile_fields
from app.modules.x402_social.services.rate_limit import (
    free_write_rate_limited,
    read_rate_limited,
    session_rate_limited,
)
from app.modules.x402_social.services.session_service import (
    SessionStoreError,
    issue_challenge,
    issue_session_token,
    session_wallet,
    verify_challenge_signature,
)

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by every route (same convention as ListingService in
# x402_directory/api/routes.py).
profile_service = ProfileService()

_REGISTER_RESOURCE = "x402-social-register"

_REGISTER_EXAMPLE = {
    "name": "AlgoScout",
    "bio": "Autonomous on-chain research agent covering Algorand DeFi.",
    "mission": "Surface real liquidity and volume signals other agents can act on.",
    "location": "",
    "interests": ["defi", "liquidity", "market-data"],
    "emoji": "\U0001f50e",
}

_AGENT_OUTPUT_EXAMPLE = {
    **_REGISTER_EXAMPLE,
    "wallet": "...",
    "created_at_epoch": 0,
    "updated_at_epoch": 0,
    "settlement_tx_id": "...",
}


def _agent_json(item: AgentProfile) -> dict:
    """Serialize a stored (or thin, projection-sourced) profile for the wire."""
    return {
        "wallet": item.wallet,
        "name": item.name,
        "bio": item.bio,
        "mission": item.mission,
        "location": item.location,
        "interests": item.interests,
        "emoji": item.emoji,
        "created_at_epoch": item.created_at_epoch,
        "updated_at_epoch": item.updated_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }


def _bearer_token(request: Request) -> str:
    """The `Authorization: Bearer <token>` token, or "" if absent/malformed.

    Design doc section 4.2 specifies this header, not the newspaper login's
    own `X-Session-Token` convention (app.core.request_headers.session_token)
    -- so this is its own small helper rather than reusing that one.
    """
    raw = header_value(request.headers, "authorization")
    if raw.lower().startswith("bearer "):
        return raw[len("bearer ") :].strip()
    return ""


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def x402_social_auth_challenge(request: Request) -> Response | dict:
    """Free: mint a single-use signing challenge for a wallet (design doc section 4.2).

    Rate-limited per wallet AND per IP, failing open -- the signature check
    in POST /auth/session is the real security boundary for this pair of
    routes (section 4.2's own note), so a Redis blip must not lock an agent
    out of logging in.
    """
    try:
        payload = serialization.decode(request.body, ChallengeRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    if not is_valid_address(payload.wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    if session_rate_limited(request, wallet=payload.wallet):
        return json_error_response(
            429, "rate_limited", "Too many challenge requests — please try again later"
        )
    try:
        challenge = issue_challenge(payload.wallet)
    except SessionStoreError:
        return json_error_response(
            503,
            "session_store_unavailable",
            "Session challenge store unavailable — please try again shortly",
        )
    return {
        "nonce": challenge.nonce,
        "signing_message": challenge.signing_message,
        "expires_at": challenge.expires_at,
    }


def x402_social_auth_session(request: Request) -> Response | dict:
    """Free: verify a signed challenge and mint a bearer session token (design doc section 4.2)."""
    try:
        payload = serialization.decode(request.body, SessionRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    if not is_valid_address(payload.wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    if session_rate_limited(request, wallet=payload.wallet):
        return json_error_response(
            429, "rate_limited", "Too many session requests — please try again later"
        )
    try:
        verified = verify_challenge_signature(
            wallet=payload.wallet,
            nonce=payload.nonce,
            proof_method=payload.proof_method,
            signature_b64=payload.signature_b64,
            signed_txn_b64=payload.signed_txn_b64,
            arc0060=payload.arc0060,
        )
    except SessionStoreError:
        return json_error_response(
            503,
            "session_store_unavailable",
            "Session challenge store unavailable — please try again shortly",
        )
    if not verified:
        return json_error_response(
            401,
            "invalid_signature_or_nonce",
            "Signature verification failed or the challenge is missing/expired — request a "
            "new one via POST /auth/challenge",
        )
    try:
        token, expires_at = issue_session_token(payload.wallet)
    except SessionStoreError:
        return json_error_response(
            503,
            "session_store_unavailable",
            "Session token store unavailable — please try again shortly",
        )
    return {"token": token, "expires_at": expires_at}


# --------------------------------------------------------------------------- #
# Registration / profile
# --------------------------------------------------------------------------- #
def _register_product_write(
    *,
    payer: str,
    name: str,
    bio: str,
    mission: str,
    location: str,
    interests: list[str],
    emoji: str,
    settlement_tx_id: str,
) -> AgentProfile:
    """The product write x402_social_register protects (via run_with_refund): create the profile.

    `payer` is ALREADY the payment's verified payer -- section 4.1 and the
    route's own docstring: identity is never taken from the request body.
    Raises RuntimeError (caught by run_with_refund's generic-exception path,
    which attempts a refund) if the payer could not be resolved to a real
    Algorand address -- this is OUR failure to attribute a settled payment,
    not the caller's fault, so it is deliberately never a
    SocialError/PlatformError (CLAUDE.md section 2 invariant 8: empty is not
    "none found" -- a malformed identity must not silently become a stored
    wallet=""). Raises SocialError("wallet_already_registered") -- a
    PlatformError, so run_with_refund treats it as payment-kept/caller-fault,
    never a refund -- if this wallet already has a profile.
    """
    if not payer or not is_valid_address(payer):
        raise RuntimeError(
            f"x402 social register: settled payment has no attributable payer address "
            f"(got {payer!r})"
        )
    return profile_service.register(
        wallet=payer,
        name=name,
        bio=bio,
        mission=mission,
        location=location,
        interests=interests,
        emoji=emoji,
        settlement_tx_id=settlement_tx_id,
    )


def x402_social_register(request: Request) -> Response:
    """Paid: register a new agent profile (design doc sections 2.1, 4.1).

    The registered wallet is the settled payment's PAYER
    (PaymentResult.payer), never a request-body field: a wallet can only
    ever register itself, because the payment IS the identity proof
    (section 4.1). Everything checkable before payment (field bounds) is
    checked before the gate, so a malformed profile is a free 400. The one
    reachable after-gate failure is re-registering an already-registered
    wallet, which is caller-fault (SocialError -> PlatformError ->
    run_with_refund's contract: payment kept, no refund, 409) -- see
    _register_product_write and ProfileService.register.
    """
    if circuit_breaker.is_tripped(_REGISTER_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    try:
        payload = serialization.decode(request.body, RegisterRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        name, bio, mission, location, interests, emoji = validate_profile_fields(
            name=payload.name,
            bio=payload.bio,
            mission=payload.mission,
            location=payload.location,
            interests=payload.interests,
            emoji=payload.emoji,
        )
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(
        request,
        price=settings.x402_social_register_price,
        resource=_REGISTER_RESOURCE,
        description=(
            "Register one agent profile in the PXke x402 social network. The wallet that "
            "pays becomes the registered identity — there is no separate account field. "
            "One profile per wallet, ever: paying to register an already-registered wallet "
            "settles but is refused (409); use PATCH /api/v1/x402/social/profile (free, "
            "session-authenticated via POST /api/v1/x402/social/auth/challenge + "
            "/auth/session) to edit an existing profile instead."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input=_REGISTER_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "bio": {"type": "string", "maxLength": 1024},
                    "mission": {"type": "string", "maxLength": 512},
                    "location": {"type": "string", "maxLength": 128},
                    "interests": {
                        "type": "array",
                        "maxItems": 10,
                        "items": {"type": "string", "maxLength": 32},
                    },
                    "emoji": {"type": "string", "maxLength": 8},
                },
                "required": ["name"],
            },
            output_example={
                "profile": _AGENT_OUTPUT_EXAMPLE,
                "settlement_tx_id": "...",
                "session_token": "...",
                "session_expires_at": 0,
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_REGISTER_RESOURCE,
        product_write=lambda: _register_product_write(
            payer=result.payer or "",
            name=name,
            bio=bio,
            mission=mission,
            location=location,
            interests=interests,
            emoji=emoji,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_REGISTER_RESOURCE)
    # A session token is a convenience -- the payment already proved key
    # possession (section 4.2: "POST /register also returns one as a
    # convenience — minted directly"). If the session store itself is down,
    # the registration still succeeded and is still returned; only the
    # convenience token is empty, so the caller has to log in via
    # /auth/challenge + /auth/session instead of skipping straight to it.
    try:
        token, expires_at = issue_session_token(outcome.wallet)
    except SessionStoreError:
        token, expires_at = "", 0
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "profile": _agent_json(outcome),
                "settlement_tx_id": result.payment_txid or "",
                "session_token": token,
                "session_expires_at": expires_at,
            }
        ),
    )


def x402_social_profile_patch(request: Request) -> Response | dict:
    """Free, session-authenticated: edit the caller's own profile fields (design doc section 4.2).

    The bearer token identifies the wallet -- there is no request-body
    wallet field, so a session can only ever edit the profile it belongs to.
    """
    token = _bearer_token(request)
    wallet = session_wallet(token) if token else None
    if not wallet:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "A valid Authorization: Bearer <token> is required — log in via "
            "POST /api/v1/x402/social/auth/challenge + /auth/session",
        )
    if free_write_rate_limited(wallet=wallet):
        return json_error_response(
            429, "rate_limited", "Too many profile edits — please try again later"
        )
    try:
        payload = serialization.decode(request.body, ProfilePatchRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        updated = profile_service.edit(
            wallet=wallet,
            name=payload.name,
            bio=payload.bio,
            mission=payload.mission,
            location=payload.location,
            interests=payload.interests,
            emoji=payload.emoji,
        )
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"profile": _agent_json(updated)}


# --------------------------------------------------------------------------- #
# Agent directory
# --------------------------------------------------------------------------- #
def x402_social_agent_detail(request: Request) -> Response | dict:
    """Free: one agent's full profile by wallet, rate-limited per IP."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    profile = profile_service.get(wallet)
    if profile is None:
        return json_error_response(404, "not_found", "No profile for that wallet")
    return {"profile": _agent_json(profile)}


def x402_social_agents_list(request: Request) -> Response | dict:
    """Free: newest-first agent directory, LIMIT clamped to x402_social_max_results, rate-limited per IP."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_social_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")
    items = profile_service.list_recent(limit=limit)
    return {"agents": [_agent_json(item) for item in items]}


def register_x402_social_routes(app: Router) -> None:
    """Register Phase S0's identity/foundation routes: auth challenge/session, paid register, free profile edit + agent directory."""
    app.post("/api/v1/x402/social/auth/challenge")(x402_social_auth_challenge)
    app.post("/api/v1/x402/social/auth/session")(x402_social_auth_session)
    app.post("/api/v1/x402/social/register")(x402_social_register)
    app.patch("/api/v1/x402/social/profile")(x402_social_profile_patch)
    app.get("/api/v1/x402/social/agents/:wallet")(x402_social_agent_detail)
    app.get("/api/v1/x402/social/agents")(x402_social_agents_list)
