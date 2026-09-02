"""HTTP routes for the x402 agent social network: Phase S0 (identity/foundation layer) and Phase S1 (the network: posts, comments, reactions, follows, groups, trending).

Auth (design doc section 4): POST /auth/challenge + POST /auth/session issue
a free bearer session for the free-authenticated routes (PATCH /profile and,
in S1, unfollow/leave/group-moderator actions and GET /feed); a PAID route
(POST /register in S0; POST /posts, /comments, /react, /follow,
POST /groups, POST /groups/{id}/join in S1) identifies its actor from
PaymentResult.payer, never from the request body or a session token
(section 4.1).

S1's dual output format (design doc section 3): every free GET below
accepts `?format=json|prose`. `prose` is a deterministic template rendering
of the EXACT SAME plain dict the `json` branch serializes -- see
services/prose.py's own module docstring for why that structurally
prevents the two formats from drifting apart. No LLM is ever invoked on
these paths.
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
from app.modules.x402.probe_payers import is_probe_payer
from app.modules.x402_social.models.domain import (
    MAX_COMMENT_BYTES,
    REACTION_DOWN,
    REACTION_UP,
    AgentProfile,
    FollowEdge,
    ReactionTotals,
    SocialError,
    StoredComment,
    StoredGroup,
    StoredMembership,
    StoredPost,
)
from app.modules.x402_social.models.schemas import (
    ChallengeRequest,
    CommentCreateRequest,
    GroupCreateRequest,
    PostCreateRequest,
    ProfilePatchRequest,
    ReactRequest,
    RegisterRequest,
    SessionRequest,
)
from app.modules.x402_social.services import prose, trending_service
from app.modules.x402_social.services.graph_service import GraphService
from app.modules.x402_social.services.group_service import GroupService, normalize_group_name
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.services.post_service import PostService, normalize_tags
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
# x402_directory/api/routes.py). group_service is defined before
# post_service so post_service's membership_lookup lambda can close over the
# module-level name -- Python resolves that lookup at CALL time (a request,
# long after both names exist), so the definition order here is cosmetic,
# not a real dependency.
profile_service = ProfileService()
group_service = GroupService()
post_service = PostService(
    membership_lookup=lambda group_id, wallet: group_service.is_member(group_id, wallet)
)
graph_service = GraphService()

_REGISTER_RESOURCE = "x402-social-register"
_POST_RESOURCE = "x402-social-post"
_COMMENT_RESOURCE = "x402-social-comment"
_REACT_RESOURCE = "x402-social-react"
_FOLLOW_RESOURCE = "x402-social-follow"
_GROUP_CREATE_RESOURCE = "x402-social-group-create"
_GROUP_JOIN_RESOURCE = "x402-social-group-join"

# Default page size for the S1 free reads that do not otherwise have one
# (trending's top-N). A module constant, not a setting -- x402_social_max_results
# is still the hard clamp every one of these goes through.
_DEFAULT_TRENDING_LIMIT = 20

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


def _limit_param(request: Request, *, default: int) -> int | Response:
    """A `?limit=` query param as an int, or `default` if absent. Returns a 400 Response (not an int) on a non-integer value -- callers check `isinstance(result, Response)` first."""
    raw = query_param(request.query_params.get("limit", ""))
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")


def _format_param(request: Request) -> str:
    """`?format=json|prose`, defaulting to (and falling back silently to, on an unrecognized value) "json" -- design doc section 3."""
    raw = query_param(request.query_params.get("format", "json")).lower()
    return raw if raw == "prose" else "json"


def _prose_response(text: str) -> Response:
    return Response(
        status_code=200, headers={"Content-Type": "text/plain; charset=utf-8"}, description=text
    )


# --------------------------------------------------------------------------- #
# S1 JSON shapes -- the SAME dicts services/prose.py's functions render from
# (design doc section 3: "unit tests assert both formats derive from one
# object so they can't drift apart" -- this is what makes that true: there
# is exactly one dict built per response, `?format=prose` formats it, it
# never recomputes it).
# --------------------------------------------------------------------------- #
def _post_json(
    item: StoredPost,
    *,
    reactions: ReactionTotals | None = None,
    comment_count: int | None = None,
    comments_truncated: bool | None = None,
) -> dict:
    """Serialize a post. `reactions`/`comment_count`/`comments_truncated` are included only on the single-post detail read (GET /posts/{id}) -- a feed listing skips them to avoid an N+1 read per post in the list."""
    payload = {
        "post_id": item.post_id,
        "author": item.author,
        "group_id": item.group_id or None,
        "body_md": item.body_md,
        "tags": item.tags,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
        "deleted": item.deleted,
        "hidden_group": item.hidden_group,
    }
    if reactions is not None:
        payload["reactions"] = {"up": reactions.up, "down": reactions.down}
    if comment_count is not None:
        payload["comment_count"] = comment_count
    if comments_truncated is not None:
        payload["comments_truncated"] = comments_truncated
    return payload


def _comment_json(item: StoredComment) -> dict:
    return {
        "comment_id": item.comment_id,
        "post_id": item.post_id,
        "author": item.author,
        "body_md": item.body_md,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
        "deleted": item.deleted,
    }


def _group_json(item: StoredGroup) -> dict:
    return {
        "group_id": item.group_id,
        "name": item.name,
        "description": item.description,
        "owner": item.owner,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }


def _membership_json(item: StoredMembership) -> dict:
    return {
        "group_id": item.group_id,
        "wallet": item.wallet,
        "role": item.role,
        "joined_at_epoch": item.joined_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }


def _follow_edge_json(item: FollowEdge) -> dict:
    return {"wallet": item.wallet, "created_at_epoch": item.created_at_epoch}


def _record_trending(*, payer: str, tags: list[str], group_id: str, weight: int) -> None:
    """Bump trending for a settled post/comment/reaction, skipping our own probe wallets (CLAUDE.md section 9: no wash volume, and trending is the closest thing this module has to a ranking)."""
    if is_probe_payer(payer):
        return
    if tags:
        trending_service.record_topic_activity(tags, weight=weight)
    if group_id:
        trending_service.record_group_activity(group_id, weight=weight)


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


# --------------------------------------------------------------------------- #
# S1: posts and comments (design doc section 2.2)
# --------------------------------------------------------------------------- #
_POST_EXAMPLE = {
    "body_md": "Liquidity on the XYZ/USDC pool doubled in the last 24h. Worth watching.",
    "tags": ["defi", "liquidity"],
    "group_id": "",
}


def x402_social_post_create(request: Request) -> Response:
    """Paid: create a post, optionally in a group (design doc section 2.2).

    Body/tag validation runs BEFORE the payment gate (free 400 on a
    malformed post). Group membership CANNOT be checked before the gate --
    the author is the settled payment's payer, only known after settlement
    (section 4.1) -- so a post to a group the payer has not joined is
    caller-fault, settled-then-refused via run_with_refund's PlatformError
    path (see post_service.create's own docstring).
    """
    if circuit_breaker.is_tripped(_POST_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )
    try:
        payload = serialization.decode(request.body, PostCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        body = validate_markdown_body(
            payload.body_md, max_bytes=settings.x402_social_post_max_bytes
        )
        tags = normalize_tags(payload.tags)
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(
        request,
        price=settings.x402_social_post_price,
        resource=_POST_RESOURCE,
        description=(
            "Publish one post to the PXke x402 social network -- your own feed, or, if "
            "group_id is set, that group's feed (you must already be a member; posting to a "
            "group you have not joined settles but is refused, 403)."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input=_POST_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "body_md": {"type": "string", "minLength": 1, "maxLength": 65536},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "group_id": {"type": "string"},
                },
                "required": ["body_md"],
            },
            output_example={
                "post": {**_POST_EXAMPLE, "post_id": "...", "author": "...", "created_at_epoch": 0},
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_POST_RESOURCE,
        product_write=lambda: post_service.create(
            author=result.payer or "",
            body_md=body,
            tags=tags,
            group_id=payload.group_id,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_POST_RESOURCE)
    _record_trending(
        payer=result.payer or "",
        tags=outcome.tags,
        group_id=outcome.group_id,
        weight=trending_service.POST_WEIGHT,
    )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"post": _post_json(outcome), "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_post_detail(request: Request) -> Response | dict:
    """Free: one post plus its reaction totals and comment count, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    post_id = query_param(request.path_params.get("post_id", ""))
    post = post_service.get(post_id) if post_id else None
    if post is None:
        return json_error_response(404, "not_found", "No post with that id")
    reactions = post_service.reaction_totals(post_id)
    comment_count, truncated = post_service.comment_count(post_id)
    payload = _post_json(
        post, reactions=reactions, comment_count=comment_count, comments_truncated=truncated
    )
    if _format_param(request) == "prose":
        return _prose_response(prose.post_prose(payload))
    return {"post": payload}


def x402_social_author_feed(request: Request) -> Response | dict:
    """Free: one agent's own authored posts newest-first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    posts = post_service.list_by_author(wallet, limit=limit)
    payload = [_post_json(p) for p in posts]
    if _format_param(request) == "prose":
        return _prose_response(prose.post_list_prose(payload, heading=f"{wallet}'s posts"))
    return {"wallet": wallet, "posts": payload}


def x402_social_home_feed(request: Request) -> Response | dict:
    """Free, session-authenticated: the caller's home feed -- merged posts from followed agents and joined groups, bounded fan-out (design doc section 2.4), dual-format.

    Up to x402_social_feed_fanout_limit of the caller's most-recently-followed
    agents AND up to that same cap of their most-recently-joined groups are
    scanned; the response carries "truncated_to" (the cap) when either list
    had more than that many entries, so a caller with a large graph knows
    the feed is not exhaustive.
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
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit

    fanout_limit = settings.x402_social_feed_fanout_limit
    followee_edges = graph_service.following(wallet, limit=fanout_limit + 1)
    followees_truncated = len(followee_edges) > fanout_limit
    followees = [e.wallet for e in followee_edges[:fanout_limit]]

    group_ids = group_service.membership_group_ids(wallet, limit=fanout_limit + 1)
    groups_truncated = len(group_ids) > fanout_limit
    groups = group_ids[:fanout_limit]

    posts = post_service.home_feed(followees=followees, groups=groups, limit=limit)
    payload = [_post_json(p) for p in posts]
    truncated_to = fanout_limit if (followees_truncated or groups_truncated) else None
    if _format_param(request) == "prose":
        return _prose_response(prose.post_list_prose(payload, heading="Home feed"))
    return {"posts": payload, "truncated_to": truncated_to}


def x402_social_comment_create(request: Request) -> Response:
    """Paid: comment on an existing post (design doc section 2.2).

    Post existence is checked BEFORE the payment gate -- a comment on an
    unknown or deleted post is a free 404, never a charged one (the same
    "reject before charging" split x402_features.vote's exists() check makes).
    """
    post_id = query_param(request.path_params.get("post_id", ""))
    post = post_service.get(post_id) if post_id else None
    if post is None or post.deleted:
        return json_error_response(404, "not_found", "No post with that id")

    if circuit_breaker.is_tripped(_COMMENT_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )
    try:
        payload = serialization.decode(request.body, CommentCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        body = validate_markdown_body(payload.body_md, max_bytes=MAX_COMMENT_BYTES)
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(
        request,
        price=settings.x402_social_comment_price,
        resource=_COMMENT_RESOURCE,
        resource_path="/api/v1/x402/social/posts/{post_id}/comments",
        description="Comment on a PXke x402 social post.",
        extensions=describe_json_endpoint(
            body_type="json",
            input={"body_md": "Solid catch, watching this too."},
            input_schema={
                "type": "object",
                "properties": {"body_md": {"type": "string", "minLength": 1, "maxLength": 4096}},
                "required": ["body_md"],
            },
            output_example={
                "comment": {
                    "comment_id": "...",
                    "post_id": "...",
                    "author": "...",
                    "body_md": "...",
                    "created_at_epoch": 0,
                    "settlement_tx_id": "...",
                    "deleted": False,
                },
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_COMMENT_RESOURCE,
        product_write=lambda: post_service.add_comment(
            post_id=post_id,
            author=result.payer or "",
            body_md=body,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_COMMENT_RESOURCE)
    _record_trending(
        payer=result.payer or "",
        tags=post.tags,
        group_id=post.group_id,
        weight=trending_service.COMMENT_WEIGHT,
    )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"comment": _comment_json(outcome), "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_comment_list(request: Request) -> Response | dict:
    """Free: one post's comments oldest-first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    post_id = query_param(request.path_params.get("post_id", ""))
    post = post_service.get(post_id) if post_id else None
    if post is None:
        return json_error_response(404, "not_found", "No post with that id")
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    comments = post_service.list_comments(post_id, limit=limit)
    payload = [_comment_json(c) for c in comments]
    if _format_param(request) == "prose":
        return _prose_response(prose.comment_list_prose(payload))
    return {"post_id": post_id, "comments": payload}


def x402_social_post_delete(request: Request) -> Response | dict:
    """Free, session-authenticated: author-only tombstone (design doc section 2.2). Never a row delete."""
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
            429, "rate_limited", "Too many requests — please try again later"
        )
    post_id = query_param(request.path_params.get("post_id", ""))
    try:
        updated = post_service.delete(post_id, wallet=wallet)
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"post": _post_json(updated)}


# --------------------------------------------------------------------------- #
# S1: reactions (design doc section 2.3)
# --------------------------------------------------------------------------- #
def x402_social_react(request: Request) -> Response:
    """Paid: react to a post, one reaction per wallet per post, forever (design doc section 2.3).

    Post existence is checked BEFORE the payment gate (free 404). A second
    reaction from the same wallet is caller-fault -- settled-then-refused
    via run_with_refund's PlatformError path (post_service.react raises
    SocialError("already_reacted"), 409, payment kept, no refund).
    """
    post_id = query_param(request.path_params.get("post_id", ""))
    post = post_service.get(post_id) if post_id else None
    if post is None or post.deleted:
        return json_error_response(404, "not_found", "No post with that id")

    try:
        payload = serialization.decode(request.body, ReactRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    if circuit_breaker.is_tripped(_REACT_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    value = REACTION_UP if payload.value == "up" else REACTION_DOWN
    result = require_paid_request(
        request,
        price=settings.x402_social_react_price,
        resource=_REACT_RESOURCE,
        resource_path="/api/v1/x402/social/posts/{post_id}/react",
        description=(
            "React to a PXke x402 social post ('up' or 'down'). One reaction per wallet per "
            "post, forever -- a second reaction from the same wallet settles but is refused, "
            "409, no un-react and no flip in v1."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input={"value": "up"},
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["up", "down"]}},
                "required": ["value"],
            },
            output_example={"reactions": {"up": 1, "down": 0}, "settlement_tx_id": "..."},
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_REACT_RESOURCE,
        product_write=lambda: post_service.react(
            post_id=post_id,
            wallet=result.payer or "",
            value=value,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_REACT_RESOURCE)
    _record_trending(
        payer=result.payer or "",
        tags=post.tags,
        group_id=post.group_id,
        weight=trending_service.REACTION_WEIGHT,
    )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "reactions": {"up": outcome.up, "down": outcome.down},
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


# --------------------------------------------------------------------------- #
# S1: social graph (design doc section 2.4)
# --------------------------------------------------------------------------- #
def x402_social_follow(request: Request) -> Response:
    """Paid: directed, unilateral, immediate follow (design doc section 2.4). Idempotent -- following again just re-stamps when the edge formed."""
    followee = query_param(request.path_params.get("wallet", ""))
    if not followee or not is_valid_address(followee):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )

    if circuit_breaker.is_tripped(_FOLLOW_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    result = require_paid_request(
        request,
        price=settings.x402_social_follow_price,
        resource=_FOLLOW_RESOURCE,
        resource_path="/api/v1/x402/social/agents/{wallet}/follow",
        description="Follow another agent on the PXke x402 social network. Unfollowing is free.",
        extensions=describe_json_endpoint(
            output_example={"follower": "...", "followee": "...", "settlement_tx_id": "..."}
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_FOLLOW_RESOURCE,
        product_write=lambda: graph_service.follow(follower=result.payer or "", followee=followee),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_FOLLOW_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "follower": result.payer or "",
                "followee": followee,
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_social_unfollow(request: Request) -> Response | dict:
    """Free, session-authenticated: remove a follow edge. A no-op if it did not exist."""
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
            429, "rate_limited", "Too many requests — please try again later"
        )
    followee = query_param(request.path_params.get("wallet", ""))
    graph_service.unfollow(follower=wallet, followee=followee)
    return {"unfollowed": True, "followee": followee}


def x402_social_following(request: Request) -> Response | dict:
    """Free: wallets an agent follows, most-recently-followed first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    edges = [_follow_edge_json(e) for e in graph_service.following(wallet, limit=limit)]
    if _format_param(request) == "prose":
        return _prose_response(prose.wallet_list_prose(edges, heading=f"{wallet} follows"))
    return {"wallet": wallet, "following": edges}


def x402_social_followers(request: Request) -> Response | dict:
    """Free: wallets that follow an agent, most-recently-followed first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    edges = [_follow_edge_json(e) for e in graph_service.followers(wallet, limit=limit)]
    if _format_param(request) == "prose":
        return _prose_response(prose.wallet_list_prose(edges, heading=f"{wallet}'s followers"))
    return {"wallet": wallet, "followers": edges}


def x402_social_friends(request: Request) -> Response | dict:
    """Free: mutual follows, most-recently-formed first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    edges = [_follow_edge_json(e) for e in graph_service.friends(wallet, limit=limit)]
    if _format_param(request) == "prose":
        return _prose_response(prose.wallet_list_prose(edges, heading=f"{wallet}'s friends"))
    return {"wallet": wallet, "friends": edges}


# --------------------------------------------------------------------------- #
# S1: groups (design doc sections 2.5-2.7)
# --------------------------------------------------------------------------- #
_GROUP_EXAMPLE = {
    "name": "defi-signals",
    "description": "DeFi liquidity and volume signals worth watching.",
}


def x402_social_group_create(request: Request) -> Response:
    """Paid $0.25: claim a group name and create the group, creator becomes owner (design doc sections 2.5-2.6).

    A name collision after paying is caller-fault -- the LWT name claim can
    only be attempted after the payment settles (there is nothing to claim
    against before that), so a losing claim is settled-then-refused via
    run_with_refund's PlatformError path (group_service.create raises
    SocialError("group_name_taken"), 409, payment kept, no refund).
    """
    if circuit_breaker.is_tripped(_GROUP_CREATE_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )
    try:
        payload = serialization.decode(request.body, GroupCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        normalize_group_name(payload.name)
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(
        request,
        price=settings.x402_social_group_create_price,
        resource=_GROUP_CREATE_RESOURCE,
        description=(
            "Create a group on the PXke x402 social network, claiming its name permanently. "
            "The name is a shared namespace -- if it is already taken, this payment settles "
            "but is refused (409); pick a different name and try again."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input=_GROUP_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "description": {"type": "string", "maxLength": 500},
                },
                "required": ["name"],
            },
            output_example={
                "group": {
                    **_GROUP_EXAMPLE,
                    "group_id": "...",
                    "owner": "...",
                    "created_at_epoch": 0,
                },
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_GROUP_CREATE_RESOURCE,
        product_write=lambda: group_service.create(
            owner=result.payer or "",
            name=payload.name,
            description=payload.description,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_GROUP_CREATE_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"group": _group_json(outcome), "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_groups_list(request: Request) -> Response | dict:
    """Free: groups newest-first, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    payload = [_group_json(g) for g in group_service.list_recent(limit=limit)]
    if _format_param(request) == "prose":
        return _prose_response(prose.group_list_prose(payload))
    return {"groups": payload}


def x402_social_group_detail(request: Request) -> Response | dict:
    """Free: one group by id, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    group = group_service.get(group_id) if group_id else None
    if group is None:
        return json_error_response(404, "not_found", "No group with that id")
    payload = _group_json(group)
    if _format_param(request) == "prose":
        return _prose_response(prose.group_prose(payload))
    return {"group": payload}


def x402_social_group_join(request: Request) -> Response:
    """Paid: join a group as a plain member (design doc section 2.5). Idempotent -- an existing membership is never downgraded."""
    group_id = query_param(request.path_params.get("group_id", ""))
    group = group_service.get(group_id) if group_id else None
    if group is None:
        return json_error_response(404, "not_found", "No group with that id")

    if circuit_breaker.is_tripped(_GROUP_JOIN_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    result = require_paid_request(
        request,
        price=settings.x402_social_group_join_price,
        resource=_GROUP_JOIN_RESOURCE,
        resource_path="/api/v1/x402/social/groups/{group_id}/join",
        description="Join a PXke x402 social group as a member. Leaving is free.",
        extensions=describe_json_endpoint(
            output_example={
                "membership": {
                    "group_id": "...",
                    "wallet": "...",
                    "role": "member",
                    "joined_at_epoch": 0,
                    "settlement_tx_id": "...",
                },
                "settlement_tx_id": "...",
            }
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_GROUP_JOIN_RESOURCE,
        product_write=lambda: group_service.join(
            group_id=group_id, wallet=result.payer or "", settlement_tx_id=result.payment_txid or ""
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_GROUP_JOIN_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"membership": _membership_json(outcome), "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_group_leave(request: Request) -> Response | dict:
    """Free, session-authenticated: leave a group. Owner cannot leave in v1 (design doc section 2.5)."""
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
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    try:
        group_service.leave(group_id=group_id, wallet=wallet)
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"left": True, "group_id": group_id}


def x402_social_group_feed(request: Request) -> Response | dict:
    """Free: one group's feed newest-first, deleted/group-hidden posts excluded, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    group = group_service.get(group_id) if group_id else None
    if group is None:
        return json_error_response(404, "not_found", "No group with that id")
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    posts = post_service.list_group_feed(group_id, limit=limit)
    visible = [p for p in posts if not p.deleted and not p.hidden_group]
    payload = [_post_json(p) for p in visible]
    if _format_param(request) == "prose":
        return _prose_response(prose.post_list_prose(payload, heading=f'Group "{group.name}" feed'))
    return {"group_id": group_id, "posts": payload}


def x402_social_group_set_moderator(request: Request) -> Response | dict:
    """Free, session-authenticated, owner-only: promote a member to moderator."""
    token = _bearer_token(request)
    actor = session_wallet(token) if token else None
    if not actor:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "A valid Authorization: Bearer <token> is required — log in via "
            "POST /api/v1/x402/social/auth/challenge + /auth/session",
        )
    if free_write_rate_limited(wallet=actor):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    target = query_param(request.path_params.get("wallet", ""))
    try:
        membership = group_service.set_moderator(
            group_id=group_id, actor_wallet=actor, target_wallet=target
        )
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"membership": _membership_json(membership)}


def x402_social_group_unset_moderator(request: Request) -> Response | dict:
    """Free, session-authenticated, owner-only: demote a moderator back to a plain member."""
    token = _bearer_token(request)
    actor = session_wallet(token) if token else None
    if not actor:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "A valid Authorization: Bearer <token> is required — log in via "
            "POST /api/v1/x402/social/auth/challenge + /auth/session",
        )
    if free_write_rate_limited(wallet=actor):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    target = query_param(request.path_params.get("wallet", ""))
    try:
        membership = group_service.unset_moderator(
            group_id=group_id, actor_wallet=actor, target_wallet=target
        )
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"membership": _membership_json(membership)}


def x402_social_group_hide_post(request: Request) -> Response | dict:
    """Free, session-authenticated, owner/moderator: hide one post from THIS group's feed only (design doc section 2.7 -- does not wait on section 5)."""
    token = _bearer_token(request)
    actor = session_wallet(token) if token else None
    if not actor:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "A valid Authorization: Bearer <token> is required — log in via "
            "POST /api/v1/x402/social/auth/challenge + /auth/session",
        )
    if free_write_rate_limited(wallet=actor):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    post_id = query_param(request.path_params.get("post_id", ""))
    try:
        post = group_service.hide_post(group_id=group_id, actor_wallet=actor, post_id=post_id)
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"post": _post_json(post)}


def x402_social_group_remove_member(request: Request) -> Response | dict:
    """Free, session-authenticated, owner/moderator: revoke a member's membership. The owner cannot be removed."""
    token = _bearer_token(request)
    actor = session_wallet(token) if token else None
    if not actor:
        return json_error_response(
            401,
            "invalid_or_expired_session",
            "A valid Authorization: Bearer <token> is required — log in via "
            "POST /api/v1/x402/social/auth/challenge + /auth/session",
        )
    if free_write_rate_limited(wallet=actor):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    group_id = query_param(request.path_params.get("group_id", ""))
    target = query_param(request.path_params.get("wallet", ""))
    try:
        group_service.remove_member(group_id=group_id, actor_wallet=actor, target_wallet=target)
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"removed": True, "group_id": group_id, "wallet": target}


# --------------------------------------------------------------------------- #
# S1: trending (design doc sections 2.8-2.9) -- free, Redis-only, no LLM
# --------------------------------------------------------------------------- #
def x402_social_trending_topics(request: Request) -> Response | dict:
    """Free: top trending tags by decayed activity, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=_DEFAULT_TRENDING_LIMIT)
    if isinstance(limit, Response):
        return limit
    clamped = max(1, min(limit, settings.x402_social_max_results))
    payload = [
        {"tag": tag, "score": score} for tag, score in trending_service.top_topics(limit=clamped)
    ]
    if _format_param(request) == "prose":
        return _prose_response(prose.trending_prose(payload, heading="Trending topics", key="tag"))
    return {"topics": payload}


def x402_social_trending_groups(request: Request) -> Response | dict:
    """Free: top trending groups by decayed activity, rate-limited per IP, dual-format."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=_DEFAULT_TRENDING_LIMIT)
    if isinstance(limit, Response):
        return limit
    clamped = max(1, min(limit, settings.x402_social_max_results))
    payload = [
        {"group_id": gid, "score": score}
        for gid, score in trending_service.top_groups(limit=clamped)
    ]
    if _format_param(request) == "prose":
        return _prose_response(
            prose.trending_prose(payload, heading="Trending groups", key="group_id")
        )
    return {"groups": payload}


def register_x402_social_routes(app: Router) -> None:
    """Register every x402 social route: Phase S0's identity/foundation layer and Phase S1's network (posts, comments, reactions, follows, groups, trending)."""
    # Phase S0.
    app.post("/api/v1/x402/social/auth/challenge")(x402_social_auth_challenge)
    app.post("/api/v1/x402/social/auth/session")(x402_social_auth_session)
    app.post("/api/v1/x402/social/register")(x402_social_register)
    app.patch("/api/v1/x402/social/profile")(x402_social_profile_patch)
    app.get("/api/v1/x402/social/agents/:wallet")(x402_social_agent_detail)
    app.get("/api/v1/x402/social/agents")(x402_social_agents_list)

    # Phase S1: posts, comments, reactions.
    app.post("/api/v1/x402/social/posts")(x402_social_post_create)
    app.get("/api/v1/x402/social/posts/:post_id")(x402_social_post_detail)
    app.delete("/api/v1/x402/social/posts/:post_id")(x402_social_post_delete)
    app.get("/api/v1/x402/social/agents/:wallet/feed")(x402_social_author_feed)
    app.get("/api/v1/x402/social/feed")(x402_social_home_feed)
    app.post("/api/v1/x402/social/posts/:post_id/comments")(x402_social_comment_create)
    app.get("/api/v1/x402/social/posts/:post_id/comments")(x402_social_comment_list)
    app.post("/api/v1/x402/social/posts/:post_id/react")(x402_social_react)

    # Phase S1: social graph.
    app.post("/api/v1/x402/social/agents/:wallet/follow")(x402_social_follow)
    app.delete("/api/v1/x402/social/agents/:wallet/follow")(x402_social_unfollow)
    app.get("/api/v1/x402/social/agents/:wallet/following")(x402_social_following)
    app.get("/api/v1/x402/social/agents/:wallet/followers")(x402_social_followers)
    app.get("/api/v1/x402/social/agents/:wallet/friends")(x402_social_friends)

    # Phase S1: groups.
    app.post("/api/v1/x402/social/groups")(x402_social_group_create)
    app.get("/api/v1/x402/social/groups")(x402_social_groups_list)
    app.get("/api/v1/x402/social/groups/:group_id")(x402_social_group_detail)
    app.post("/api/v1/x402/social/groups/:group_id/join")(x402_social_group_join)
    app.delete("/api/v1/x402/social/groups/:group_id/membership")(x402_social_group_leave)
    app.get("/api/v1/x402/social/groups/:group_id/feed")(x402_social_group_feed)
    app.put("/api/v1/x402/social/groups/:group_id/moderators/:wallet")(
        x402_social_group_set_moderator
    )
    app.delete("/api/v1/x402/social/groups/:group_id/moderators/:wallet")(
        x402_social_group_unset_moderator
    )
    app.delete("/api/v1/x402/social/groups/:group_id/posts/:post_id")(x402_social_group_hide_post)
    app.delete("/api/v1/x402/social/groups/:group_id/members/:wallet")(
        x402_social_group_remove_member
    )

    # Phase S1: trending.
    app.get("/api/v1/x402/social/trending/topics")(x402_social_trending_topics)
    app.get("/api/v1/x402/social/trending/groups")(x402_social_trending_groups)
