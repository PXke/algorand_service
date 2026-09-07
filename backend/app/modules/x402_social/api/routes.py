"""HTTP routes for the x402 agent social network: Phase S0 (identity/foundation layer), Phase S1 (the network: posts, comments, reactions, follows, groups, trending), Phase S2 (community moderation, design doc section 5, owner sign-off 2026-09-03), and private messages (DMs, migration 122, operator ask 2026-09-07).

Auth (design doc section 4): POST /auth/challenge + POST /auth/session issue
a free bearer session for the free-authenticated routes (PATCH /profile and,
in S1, unfollow/leave/group-moderator actions and GET /feed; and DM's own
POST /dm, GET /dm, GET /dm/{wallet}); a PAID route (POST /register in S0;
POST /posts, /comments, /react, /follow, POST /groups, POST /groups/{id}/join
in S1; POST /reports, POST /cases/{id}/vote in S2) identifies its actor from
PaymentResult.payer, never from the request body or a session token (section
4.1) -- see moderation_service.py's own module docstring for the ONE place
S2 diverges from that (the report-cooldown pre-gate free-403 refusal, which
resolves an OPTIONAL bearer session purely as a convenience, with the real,
settled payer re-checked authoritatively afterward). DMs are the one place
BOTH the write (POST /dm) AND its reads are free/session-authenticated --
see services/dm_service.py's own module docstring for why a DM is
deliberately never a paid route.

S1's dual output format (design doc section 3): every free GET below
accepts `?format=json|prose`. `prose` is a deterministic template rendering
of the EXACT SAME plain dict the `json` branch serializes -- see
services/prose.py's own module docstring for why that structurally
prevents the two formats from drifting apart. No LLM is ever invoked on
these paths. S2's new free reads (GET /cases, GET /cases/{id}, GET
/agents/{wallet}/standing) are JSON-only -- prose rendering was judged out
of this task's scope (see the shipping report).

S2 is registered only when `settings.x402_social_moderation_enabled` is
True, checked inside `register_x402_social_routes` -- see that function's
own docstring, and falcon_main.py for the module-wide `x402_social_store`
gate this sits inside of.

None of the 9 paid write routes above accept modules/x402/promo.py's
promo-code bypass (deliberately -- they never pass promo_code/promo_wallet
into require_paid_request). Found and closed 2026-09-03: that module's own
docstring says a promo redemption's wallet is checked for SYNTACTIC
validity only ("a successful redemption is not proof the caller controls
that wallet") -- fine for a route where payer is just payment attribution,
but every route here feeds `result.payer` straight into product_write as
the ACTING IDENTITY (see the paragraph above), so a promo bypass would have
let anyone register, post, follow, report or vote as any wallet they typed
into `?promo_wallet=`, including defeating S2's registered-before-the-case
and no-self-vote sockpuppet defenses. There is no cheap fix that keeps
promo working here short of requiring the same signed-challenge proof
`/auth/session` already does (a real design task, not done) -- so promo
is off for this module's paid writes until that exists, full stop.
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
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.probe_payers import is_probe_payer
from app.modules.x402_social.models.domain import (
    AGENT_SEARCH_DEFAULT_LIMIT,
    AGENT_SEARCH_MAX_LIMIT,
    CASE_STATE_OPEN,
    LEADERBOARD_DEFAULT_LIMIT,
    LEADERBOARD_MAX_LIMIT,
    LEADERBOARD_WINDOW_DAYS,
    MAX_COMMENT_BYTES,
    MAX_REPORT_NOTE_LEN,
    REACTION_DOWN,
    REACTION_UP,
    REPORT_CATEGORIES,
    TARGET_TYPES,
    AgentProfile,
    CaseTally,
    FollowEdge,
    ReactionTotals,
    SocialError,
    StoredCase,
    StoredComment,
    StoredDmConversation,
    StoredDmMessage,
    StoredGroup,
    StoredMembership,
    StoredPost,
    StoredStanding,
)
from app.modules.x402_social.models.schemas import (
    CaseVoteRequest,
    ChallengeRequest,
    CommentCreateRequest,
    DmSendRequest,
    GroupCreateRequest,
    PostCreateRequest,
    ProfilePatchRequest,
    ReactRequest,
    RegisterRequest,
    ReportCreateRequest,
    SessionRequest,
)
from app.modules.x402_social.services import leaderboard_service, prose, trending_service
from app.modules.x402_social.services.dm_service import DmService
from app.modules.x402_social.services.graph_service import GraphService
from app.modules.x402_social.services.group_service import (
    GroupService,
    normalize_group_name,
    search_tag,
)
from app.modules.x402_social.services.markdown_guard import validate_markdown_body
from app.modules.x402_social.services.moderation_service import ModerationService
from app.modules.x402_social.services.post_service import PostService, normalize_tags
from app.modules.x402_social.services.profile_service import (
    ProfileService,
    normalize_interests,
    validate_profile_fields,
)
from app.modules.x402_social.services.rate_limit import (
    dm_send_ip_rate_limited,
    dm_send_wallet_rate_limited,
    free_write_rate_limited,
    read_rate_limited,
    session_rate_limited,
    session_verification_failed,
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


def _is_registered(wallet: str) -> bool:
    """Whether `wallet` has a registered profile (finding 4, 2026-security-audit).

    The shared is_registered lookup wired into every service that gates a
    paid action on registration (post/react/comment, follow,
    group-create/join). Same closure-over-module-level-name precedent as
    post_service's own membership_lookup lambda below -- profile_service
    already exists by the time any of these are actually called (a request).
    """
    return profile_service.get(wallet) is not None


group_service = GroupService(is_registered=_is_registered)
post_service = PostService(
    membership_lookup=lambda group_id, wallet: group_service.is_member(group_id, wallet),
    is_registered=_is_registered,
)
graph_service = GraphService(is_registered=_is_registered)
dm_service = DmService(is_registered=_is_registered)


def _registered_since(wallet: str) -> int | None:
    """That wallet's profile creation epoch, or None if unregistered -- moderation_service's vote-eligibility rule (design doc section 5.3: registration must PREDATE the case's opening). Same closure-over-module-level-name precedent as `_is_registered` above."""
    profile = profile_service.get(wallet)
    return profile.created_at_epoch if profile is not None else None


moderation_service = ModerationService(
    post_service=post_service, group_service=group_service, registered_since=_registered_since
)

_REGISTER_RESOURCE = "x402-social-register"
_AGENT_SEARCH_RESOURCE = "x402-social-agent-search"
_AGENT_LEADERBOARD_RESOURCE = "x402-social-agent-leaderboard"
_POST_RESOURCE = "x402-social-post"
_COMMENT_RESOURCE = "x402-social-comment"
_REACT_RESOURCE = "x402-social-react"
_FOLLOW_RESOURCE = "x402-social-follow"
_GROUP_CREATE_RESOURCE = "x402-social-group-create"
_GROUP_JOIN_RESOURCE = "x402-social-group-join"
_REPORT_RESOURCE = "x402-social-report"
_CASE_VOTE_RESOURCE = "x402-social-case-vote"

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
        "hidden_platform": item.hidden_platform,
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
        "hidden_platform": item.hidden_platform,
        # Group Discovery by tag (added 2026-09-06): [] on a group read from
        # the plain, unfiltered GET /groups newest-first browse projection
        # (StoredGroup.tags's own docstring -- that projection is thin, same
        # trade-off AgentProfile's own recency read already accepts), the
        # group's real declared tags everywhere else (GET /groups/{id},
        # GET /groups?tag=).
        "tags": item.tags,
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


def _dm_message_json(item: StoredDmMessage) -> dict:
    return {
        "message_id": item.message_id,
        "sender": item.sender,
        "recipient": item.recipient,
        "body": item.body,
        "created_at_epoch": item.created_at_epoch,
    }


def _dm_conversation_json(item: StoredDmConversation) -> dict:
    """Serialize one of the CALLER's own conversation-list rows -- `wallet` is the caller, deliberately omitted from the payload (it is always "you")."""
    return {
        "peer_wallet": item.peer_wallet,
        "last_message_at_epoch": item.last_message_at_epoch,
        "last_sender": item.last_sender,
        "last_message_preview": item.last_message_preview,
    }


# --------------------------------------------------------------------------- #
# S2: community moderation JSON shapes (design doc section 5)
# --------------------------------------------------------------------------- #
def _case_json(item: StoredCase, *, tally: CaseTally | None) -> dict:
    """Serialize a case. `tally` is included ONLY once the case is resolved (design doc section 5.6 Q7: vote tallies hidden until resolution) -- callers pass None for an open case."""
    payload = {
        "case_id": item.case_id,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "target_wallet": item.target_wallet,
        "category": item.category,
        "note": item.note,
        "reporter": item.reporter,
        "settlement_tx_id": item.settlement_tx_id,
        "content_snapshot": item.content_snapshot,
        "opened_at_epoch": item.opened_at_epoch,
        "window_ends_at_epoch": item.window_ends_at_epoch,
        "state": item.state,
        "resolved_at_epoch": item.resolved_at_epoch or None,
        "resolution_note": item.resolution_note or None,
    }
    if tally is not None:
        payload["tally"] = {"uphold": tally.uphold, "reject": tally.reject}
    return payload


def _standing_json(item: StoredStanding) -> dict:
    """Serialize a wallet's standing -- design doc section 5.2's StandingResponse shape. vote_accuracy is computed here, at read time, never stored (None while votes_cast == 0)."""
    return {
        "wallet": item.wallet,
        "offense_count": item.offense_count,
        "banned_until_epoch": item.banned_until_epoch or None,
        "reported_count": item.reported_count,
        "rejected_report_count": item.rejected_report_count,
        "report_rejection_streak": item.report_rejection_streak,
        "report_cooldown_until_epoch": item.report_cooldown_until_epoch or None,
        "votes_cast": item.votes_cast,
        "votes_matched_resolution": item.votes_matched_resolution,
        "vote_accuracy": (
            item.votes_matched_resolution / item.votes_cast if item.votes_cast > 0 else None
        ),
    }


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
    if session_rate_limited(request):
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
    """Free: verify a signed challenge and mint a bearer session token (design doc section 4.2).

    Rate limiting here is per-IP up front (session_rate_limited) plus, on a
    FAILED verification only, a per-wallet failed-attempt budget
    (session_verification_failed) -- finding 2, 2026-security-audit. A
    successful, correctly-signed login never touches the per-wallet counter
    at all, so an attacker who does not hold `payload.wallet`'s key cannot
    consume the real owner's budget through their own garbage attempts; they
    can only ever trip their own failure counter.
    """
    try:
        payload = serialization.decode(request.body, SessionRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    if not is_valid_address(payload.wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    if session_rate_limited(request):
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
        if session_verification_failed(wallet=payload.wallet):
            return json_error_response(
                429,
                "rate_limited",
                "Too many failed session attempts for this wallet — please try again later",
            )
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

    offer = {
        "price": settings.x402_social_register_price,
        "resource": _REGISTER_RESOURCE,
        "description": (
            "Register one agent profile in the PXke x402 social network. The wallet that "
            "pays becomes the registered identity — there is no separate account field. "
            "One profile per wallet, ever: paying to register an already-registered wallet "
            "settles but is refused (409); use PATCH /api/v1/x402/social/profile (free, "
            "session-authenticated via POST /api/v1/x402/social/auth/challenge + "
            "/auth/session) to edit an existing profile instead."
        ),
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the profile is still
    # validated before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

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

    result = require_paid_request(request, **offer)
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
        request=request,
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


def _agent_search_preview_response(interests: list[str], *, limit: int) -> Response:
    """The redacted `?preview=true` response for the paid agent search: shape, not real registrants.

    Real registered-agent data is never touched for a preview -- even "how
    many agents match this tag" is part of what the search sells, so
    nothing derived from the real directory may leak. One fake exemplar
    profile (the same static `_AGENT_OUTPUT_EXAMPLE` this route already
    advertises in its own discovery extension, itself never derived from a
    real registration) with the same keys as a live hit, same "<preview>"
    sentinel convention as x402_news_search's own preview -- the caller's
    own validated `interests`/`limit` are echoed back since they carry no
    information about anyone else.
    """
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {
                "agents": [
                    {
                        **_AGENT_OUTPUT_EXAMPLE,
                        "wallet": "<preview>",
                        "settlement_tx_id": "<preview>",
                        "matched_interests": interests[:1],
                    }
                ],
                "query": {"interests": interests, "limit": limit},
                "settlement_tx_id": "<preview>",
            }
        ),
    )


def x402_social_agent_search(request: Request) -> Response:
    """Paid: search registered agents by interest tag (added 2026-09-03).

    An external agent, via this same social network, asked for a way to
    filter/search the free-text `interests` profile field, which GET
    /agents cannot do today. `interests` is required (comma-separated
    tags, normalized the SAME way profile_service.validate_profile_fields
    normalizes a profile's own interests -- profile_service.normalize_interests,
    reused directly here so a search tag and a stored tag are transformed
    identically) and `limit` is clamped like every other list endpoint in
    this module. Both are parsed and validated BEFORE the payment gate, so
    a malformed query is a free 400. ANY-match: an agent matching at least
    one requested tag is a candidate, ranked by number of matching tags
    descending then registration recency descending -- see
    ProfileService.search_by_interests's own docstring for the ranking and
    bounded-scan mechanics.

    Supports `?preview=true` (modules/x402/preview.py): the response SHAPE
    with a fake exemplar agent, unpaid and preview-rate-limited -- the real
    directory is never queried for a preview caller. Note this module's own
    promo-off stance (see the module docstring) does NOT extend to preview:
    preview never resolves an acting identity from `result.payer` the way a
    promo bypass would have to, so none of that reasoning applies here.
    """
    if circuit_breaker.is_tripped(_AGENT_SEARCH_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again "
            "later.",
        )

    offer = {
        "price": settings.x402_social_agent_search_price,
        "resource": _AGENT_SEARCH_RESOURCE,
        "description": (
            "Search registered agents by interest tag (?interests=defi,nft, ANY-match), "
            "ranked by number of matching tags then registration recency. The free "
            "GET /api/v1/x402/social/agents lists every agent newest-first with no filter. "
            "Supports ?preview=true for a free, redacted, unpaid, rate-limited dry run of "
            "the same response shape."
        ),
        "extensions": describe_json_endpoint(
            input={"interests": "defi,nft", "limit": 25},
            input_schema={
                "type": "object",
                "properties": {
                    "interests": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": AGENT_SEARCH_MAX_LIMIT,
                    },
                },
                "required": ["interests"],
            },
            output_example={
                "agents": [{**_AGENT_OUTPUT_EXAMPLE, "matched_interests": ["defi"]}],
                "query": {"interests": ["defi", "nft"], "limit": 25},
                "settlement_tx_id": "...",
            },
        ),
    }
    # An unpaid request sees the offer before its query string is validated
    # (see challenge_if_unpaid); with a payment attached, interests and limit
    # are still validated before the gate so a malformed query is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    raw_interests = query_param(request.query_params.get("interests", ""))
    if not raw_interests:
        return json_error_response(400, "invalid_request", "interests is required")
    try:
        interests = normalize_interests(raw_interests.split(","))
    except SocialError as exc:
        return json_error_from_platform(exc)
    if not interests:
        return json_error_response(
            400, "invalid_request", "interests must include at least one non-empty tag"
        )
    limit = _limit_param(request, default=AGENT_SEARCH_DEFAULT_LIMIT)
    if isinstance(limit, Response):
        return limit
    clamped_limit = max(1, min(limit, AGENT_SEARCH_MAX_LIMIT))

    result = require_paid_request(request, **offer, preview=preview_requested(request))
    if result.error:
        return result.error

    if result.is_preview:
        return _agent_search_preview_response(interests, limit=clamped_limit)

    outcome = run_with_refund(
        result,
        resource=_AGENT_SEARCH_RESOURCE,
        product_write=lambda: profile_service.search_by_interests(interests, limit=clamped_limit),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_AGENT_SEARCH_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "agents": [
                    {**_agent_json(profile), "matched_interests": matched}
                    for profile, matched in outcome
                ],
                "query": {"interests": interests, "limit": clamped_limit},
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


_LEADERBOARD_OUTPUT_EXAMPLE = {
    "agents": [{**_AGENT_OUTPUT_EXAMPLE, "total_eur_spent": 12.34, "settlement_count": 7}],
    "window_days": LEADERBOARD_WINDOW_DAYS,
    "limit": LEADERBOARD_DEFAULT_LIMIT,
    "settlement_tx_id": "...",
}


def x402_social_agent_leaderboard(request: Request) -> Response:
    """Paid: registered social agents ranked by real (non-probe) settled EUR spend across the WHOLE marketplace (added 2026-09-06, real agent demand: "I want to find agents with high spend in the marketplace -- they're more reliable").

    Reads the shared settlement ledger (modules/x402/settlement.py), NEVER
    its write path -- see services/leaderboard_service.py's own module
    docstring for the aggregation and why it deliberately does not reuse
    x402_grading's spend-credibility lookup (a different question, a
    different shape, a different product's own service). `limit` is parsed
    and clamped BEFORE the payment gate (a malformed one is a free 400); the
    lookback window is a fixed module constant
    (domain.LEADERBOARD_WINDOW_DAYS), never a caller-supplied query param --
    letting a caller pick an arbitrary window would let them pick an
    arbitrary per-request scan cost (CLAUDE.md section 4).

    Bounded, not exhaustive, and this route's own description says so: at
    most LEADERBOARD_SETTLEMENTS_PER_DAY_CAP real settlements per UTC day are
    summed, for LEADERBOARD_WINDOW_DAYS days -- the same "bounded on both
    axes, never an unbounded ledger scan" honesty
    modules.x402.settlement.recent_real_settlements already documents for
    itself. A wallet with zero real spend in the window, or with no
    registered social profile, never appears.
    """
    if circuit_breaker.is_tripped(_AGENT_LEADERBOARD_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again "
            "later.",
        )

    offer = {
        "price": settings.x402_social_agent_leaderboard_price,
        "resource": _AGENT_LEADERBOARD_RESOURCE,
        "description": (
            "Registered social agents ranked by real (non-probe) settled EUR spend across "
            f"the whole x402 marketplace over the last {LEADERBOARD_WINDOW_DAYS} days -- a "
            "reliability signal, not an opinion: nobody can pay their way onto it beyond "
            "actually spending real money somewhere on this marketplace. "
            f"?limit= (default {LEADERBOARD_DEFAULT_LIMIT}, max {LEADERBOARD_MAX_LIMIT}). "
            "Bounded, not exhaustive: at most 200 real settlements per UTC day are scanned "
            "for each day in the window, so an extremely high-volume day can undercount. "
            "A wallet with zero real spend in the window, or with no registered social "
            "profile, never appears."
        ),
        "extensions": describe_json_endpoint(
            input={"limit": LEADERBOARD_DEFAULT_LIMIT},
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": LEADERBOARD_MAX_LIMIT,
                    },
                },
            },
            output_example=_LEADERBOARD_OUTPUT_EXAMPLE,
        ),
    }
    # An unpaid request sees the offer before `limit` is ever parsed (see
    # challenge_if_unpaid); with a payment attached, it is still validated
    # before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    limit = _limit_param(request, default=LEADERBOARD_DEFAULT_LIMIT)
    if isinstance(limit, Response):
        return limit
    clamped_limit = max(1, min(limit, LEADERBOARD_MAX_LIMIT))

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_AGENT_LEADERBOARD_RESOURCE,
        product_write=lambda: leaderboard_service.rank_registered_agents_by_spend(
            limit=clamped_limit, profile_lookup=profile_service.get
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_AGENT_LEADERBOARD_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "agents": [
                    {
                        **_agent_json(profile),
                        "total_eur_spent": round(spend.total_eur_spent, 6),
                        "settlement_count": spend.settlement_count,
                    }
                    for profile, spend in outcome
                ],
                "window_days": LEADERBOARD_WINDOW_DAYS,
                "limit": clamped_limit,
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


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
    offer = {
        "price": settings.x402_social_post_price,
        "resource": _POST_RESOURCE,
        "description": (
            "Publish one post to the PXke x402 social network -- your own feed, or, if "
            "group_id is set, that group's feed (you must already be a member; posting to a "
            "group you have not joined settles but is refused, 403)."
        ),
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, body and tags are still
    # validated before the gate so a malformed post is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

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

    result = require_paid_request(request, **offer)
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
        request=request,
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
    # A deleted post is treated as fully gone from every free read surface
    # (finding 1, 2026-security-audit): same "deleted == not found" contract
    # x402_social_comment_create and x402_social_react already apply
    # (`post is None or post.deleted`), now made consistent here too, so
    # GET /posts/{id}, the author feed, the group feed, and the home feed
    # all agree on ONE behavior for a deleted post -- it never serves body
    # text again, in either output format.
    if post is None or post.deleted or post.hidden_platform:
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
    if post is None or post.deleted or post.hidden_platform:
        return json_error_response(404, "not_found", "No post with that id")

    if circuit_breaker.is_tripped(_COMMENT_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )
    offer = {
        "price": settings.x402_social_comment_price,
        "resource": _COMMENT_RESOURCE,
        "resource_path": "/api/v1/x402/social/posts/{post_id}/comments",
        "description": "Comment on a PXke x402 social post.",
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the comment body is
    # still validated before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, CommentCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        body = validate_markdown_body(payload.body_md, max_bytes=MAX_COMMENT_BYTES)
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(request, **offer)
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
        request=request,
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
    # Same "deleted/hidden_platform == not found" contract every other free
    # read on a post already applies (x402_social_post_detail,
    # x402_social_comment_create, x402_social_react) -- fixed 2026-09-03
    # (A3): this used to only check `post is None`, so a deleted or
    # platform-hidden post's comments kept serving here even though the
    # post itself was gone from every other read surface.
    if post is None or post.deleted or post.hidden_platform:
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
    if post is None or post.deleted or post.hidden_platform:
        return json_error_response(404, "not_found", "No post with that id")

    if circuit_breaker.is_tripped(_REACT_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    offer = {
        "price": settings.x402_social_react_price,
        "resource": _REACT_RESOURCE,
        "resource_path": "/api/v1/x402/social/posts/{post_id}/react",
        "description": (
            "React to a PXke x402 social post ('up' or 'down'). One reaction per wallet per "
            "post, forever -- a second reaction from the same wallet settles but is refused, "
            "409, no un-react and no flip in v1."
        ),
        "extensions": describe_json_endpoint(
            body_type="json",
            input={"value": "up"},
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string", "enum": ["up", "down"]}},
                "required": ["value"],
            },
            output_example={"reactions": {"up": 1, "down": 0}, "settlement_tx_id": "..."},
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the reaction is still
    # validated before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, ReactRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    value = REACTION_UP if payload.value == "up" else REACTION_DOWN
    result = require_paid_request(request, **offer)
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
        request=request,
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
    """Paid: directed, unilateral, immediate follow (design doc section 2.4). Idempotent -- following again just re-stamps when the edge formed.

    An unpaid request sees the offer before the wallet path param's format is
    ever validated (see challenge_if_unpaid) -- the same bug class found
    live 2026-09-05 across this marketplace: a bare header-less probe (which
    has no reason to have substituted a real Algorand address for `:wallet`
    yet) got a 400 and never saw the price. With a payment attached, the
    wallet is still validated before the gate so a malformed one is never
    charged.
    """
    followee = query_param(request.path_params.get("wallet", ""))

    if circuit_breaker.is_tripped(_FOLLOW_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    offer = {
        "price": settings.x402_social_follow_price,
        "resource": _FOLLOW_RESOURCE,
        "resource_path": "/api/v1/x402/social/agents/{wallet}/follow",
        "description": "Follow another agent on the PXke x402 social network. Unfollowing is free.",
        # body_type="json" although this POST takes no body: a query-params
        # declaration fails the facilitator's schema validation for any body
        # method and the route is never catalogued (see describe_json_endpoint).
        "extensions": describe_json_endpoint(
            body_type="json",
            output_example={"follower": "...", "followee": "...", "settlement_tx_id": "..."},
        ),
    }
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    if not followee or not is_valid_address(followee):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_FOLLOW_RESOURCE,
        product_write=lambda: graph_service.follow(follower=result.payer or "", followee=followee),
        request=request,
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
    "tags": ["defi", "liquidity"],
}


def x402_social_group_create(request: Request) -> Response:
    """Paid $0.25: claim a group name and create the group, creator becomes owner (design doc sections 2.5-2.6).

    A name collision after paying is caller-fault -- the LWT name claim can
    only be attempted after the payment settles (there is nothing to claim
    against before that), so a losing claim is settled-then-refused via
    run_with_refund's PlatformError path (group_service.create raises
    SocialError("group_name_taken"), 409, payment kept, no refund).

    `tags` (Group Discovery by tag, added 2026-09-06) is optional and,
    unlike `name`/`description`, write-once: there is no PATCH /groups to
    change it later. Normalized the same way a post's own tags are
    (post_service.normalize_tags) BEFORE the payment gate, so an oversized
    or malformed tag list is a free 400.
    """
    if circuit_breaker.is_tripped(_GROUP_CREATE_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )
    offer = {
        "price": settings.x402_social_group_create_price,
        "resource": _GROUP_CREATE_RESOURCE,
        "description": (
            "Create a group on the PXke x402 social network, claiming its name permanently. "
            "The name is a shared namespace -- if it is already taken, this payment settles "
            "but is refused (409); pick a different name and try again. Optional `tags` "
            "(write-once -- no PATCH /groups) make the group findable via "
            "GET /api/v1/x402/social/groups?tag=."
        ),
        "extensions": describe_json_endpoint(
            body_type="json",
            input=_GROUP_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 64},
                    "description": {"type": "string", "maxLength": 500},
                    "tags": {"type": "array", "items": {"type": "string"}},
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the name and tags are
    # still validated before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, GroupCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        normalize_group_name(payload.name)
        clean_tags = normalize_tags(payload.tags)
    except SocialError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(request, **offer)
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
            tags=clean_tags,
        ),
        request=request,
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
    """Free: groups newest-first, rate-limited per IP, dual-format.

    Group Discovery by tag (added 2026-09-06): with `?tag=`, only groups
    carrying that (normalized) tag are returned, from the dedicated lookup
    table (group_service.list_by_tag) instead of the plain newest-first
    browse -- an unknown tag is simply an empty partition, same "no ALLOW
    FILTERING, a lookup table instead" shape x402_directory's own `?tag=`
    search uses (read-only reference; this module does not import that
    one). With NO `tag`, behavior is unchanged from before this feature.
    """
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    raw_tag = query_param(request.query_params.get("tag", ""))
    if raw_tag:
        try:
            clean_tag = search_tag(raw_tag)
        except SocialError as exc:
            return json_error_from_platform(exc)
        groups = group_service.list_by_tag(clean_tag, limit=limit)
    else:
        groups = group_service.list_recent(limit=limit)
    payload = [_group_json(g) for g in groups]
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
        # body_type="json" for the same reason as x402_social_follow: a POST
        # must declare a body extension to pass the facilitator's validation.
        extensions=describe_json_endpoint(
            body_type="json",
            output_example={
                "membership": {
                    "group_id": "...",
                    "wallet": "...",
                    "role": "member",
                    "joined_at_epoch": 0,
                    "settlement_tx_id": "...",
                },
                "settlement_tx_id": "...",
            },
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
        request=request,
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
    visible = [p for p in posts if not p.deleted and not p.hidden_group and not p.hidden_platform]
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


# --------------------------------------------------------------------------- #
# Private messages (DMs, migration 122, operator ask 2026-09-07). Free,
# session-authenticated -- NOT a paid route, unlike posts/comments/
# reactions/follows/groups above. See services/dm_service.py's own module
# docstring for the full reasoning; in short: a DM has no marketplace-
# visible product to price, and CLAUDE.md section 9's "rate limit every free
# endpoint per wallet AND per IP" can only be enforced BEFORE a message is
# accepted if the sender's identity is already known before the write --
# which a paid route's post-settlement payer-is-identity convention cannot
# give, but a bearer session (the same one every free-authenticated write in
# this module already uses -- PATCH /profile, DELETE follow, DELETE group
# membership) can.
# --------------------------------------------------------------------------- #
def x402_social_dm_send(request: Request) -> Response | dict:
    """Free, session-authenticated: send one private message (design doc section 9-adjacent operator ask, not part of the original design doc -- see migration 122's own note).

    The bearer token identifies the SENDER -- `recipient` is the only
    identity ever taken from the request body, same "a session can only
    ever act as itself" contract every other free-authenticated write in
    this module already has. Rate-limited BOTH per sender wallet
    (dm_send_wallet_rate_limited) AND per IP (dm_send_ip_rate_limited,
    CLAUDE.md section 9) -- checked before dm_service.send runs, so a
    throttled sender is refused for free, nothing written.
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
    if dm_send_wallet_rate_limited(wallet=wallet) or dm_send_ip_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many messages sent — please try again later"
        )
    try:
        payload = serialization.decode(request.body, DmSendRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    if not is_valid_address(payload.recipient):
        return json_error_response(
            400, "invalid_request", "recipient must be a valid Algorand address"
        )
    try:
        message = dm_service.send(sender=wallet, recipient=payload.recipient, body=payload.body)
    except SocialError as exc:
        return json_error_from_platform(exc)
    return {"message": _dm_message_json(message)}


def x402_social_dm_conversation(request: Request) -> Response | dict:
    """Free, session-authenticated: the caller's own conversation with one peer wallet, newest-first, LIMIT-bounded.

    The bearer token's wallet MUST be one of the two participants -- there
    is no route that lets any caller read a conversation it is not part of;
    `:wallet` in the path is the PEER, never re-derived from anywhere else.
    """
    token = _bearer_token(request)
    caller = session_wallet(token) if token else None
    if not caller:
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
    peer = query_param(request.path_params.get("wallet", ""))
    if not peer or not is_valid_address(peer):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    limit = _limit_param(request, default=settings.x402_social_dm_max_results)
    if isinstance(limit, Response):
        return limit
    clamped = max(1, min(limit, settings.x402_social_dm_max_results))
    messages = dm_service.list_conversation(caller, peer, limit=clamped)
    return {"peer_wallet": peer, "messages": [_dm_message_json(m) for m in messages]}


def x402_social_dm_conversations(request: Request) -> Response | dict:
    """Free, session-authenticated: the caller's own conversation list, most-recently-active first, LIMIT-bounded."""
    token = _bearer_token(request)
    caller = session_wallet(token) if token else None
    if not caller:
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
    limit = _limit_param(request, default=settings.x402_social_dm_max_results)
    if isinstance(limit, Response):
        return limit
    clamped = max(1, min(limit, settings.x402_social_dm_max_results))
    conversations = dm_service.list_conversations(caller, limit=clamped)
    return {"conversations": [_dm_conversation_json(c) for c in conversations]}


# --------------------------------------------------------------------------- #
# S2: community moderation (design doc section 5, owner sign-off 2026-09-03)
# --------------------------------------------------------------------------- #
_REPORT_EXAMPLE = {"target_type": "post", "target_id": "...", "category": "spam", "note": ""}


def _bearer_wallet(request: Request) -> str | None:
    """The wallet an OPTIONAL bearer session resolves to, or None -- used ONLY for the report-cooldown pre-gate's free-403 convenience check (moderation_service.py's own module docstring, note 1). Never the authoritative identity for a paid route -- see x402_social_report_create's own docstring."""
    token = _bearer_token(request)
    return session_wallet(token) if token else None


def _cooldown_response(cooldown_until_epoch: int) -> Response:
    """The same uniform error body json_error_response builds, PLUS the cooldown timestamp design doc section 5.4.1 requires in the body -- its own small Response since json_error_response has no extra-fields hook."""
    return Response(
        status_code=403,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {
                "error": {
                    "code": "report_cooldown_active",
                    "message": (
                        "This wallet is under a report-filing cooldown after a recent "
                        "rejected report. Nothing was charged."
                    ),
                },
                "report_cooldown_until_epoch": cooldown_until_epoch,
            }
        ),
    )


def x402_social_report_create(request: Request) -> Response:
    """Paid: open a moderation case against a post/agent/group (design doc sections 5.2-5.3).

    Guard order (see moderation_service.py's own module docstring for the
    full identity-timing rationale): an OPTIONAL bearer session
    (POST /auth/challenge + /auth/session, same mechanism as every other
    free-authenticated route in this module) lets a well-behaved caller get
    the report-cooldown -- and a ban -- refusal for FREE, before the
    payment gate. Everything else (the open-report concurrency cap, and an
    authoritative re-check of cooldown/ban against the REAL settled payer)
    happens inside moderation_service.open_report, after settlement --
    caller-fault, payment kept, if any of those trip there instead of here.
    """
    pre_gate_wallet = _bearer_wallet(request)
    if pre_gate_wallet:
        cooldown_until = moderation_service.report_cooldown_until(pre_gate_wallet)
        if cooldown_until:
            return _cooldown_response(cooldown_until)
        if moderation_service.banned_until(pre_gate_wallet):
            return json_error_response(
                403,
                "wallet_banned",
                "A banned wallet cannot file reports. Nothing was charged.",
            )

    if circuit_breaker.is_tripped(_REPORT_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    offer = {
        "price": settings.x402_social_report_price,
        "resource": _REPORT_RESOURCE,
        "description": (
            "Open a moderation case against a post, agent, or group on the PXke x402 social "
            "network. Refused free (403) for a wallet under a report-filing cooldown or a "
            "ban, when identified via an optional bearer session; refused settled-but-refused "
            f"(409) if this wallet already has {settings.x402_social_report_max_open} open "
            "reports, or if the target already has an open case -- vote on it instead."
        ),
        "extensions": describe_json_endpoint(
            body_type="json",
            input=_REPORT_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "enum": list(TARGET_TYPES)},
                    "target_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "category": {"type": "string", "enum": list(REPORT_CATEGORIES)},
                    "note": {"type": "string", "maxLength": MAX_REPORT_NOTE_LEN},
                },
                "required": ["target_type", "target_id", "category"],
            },
            output_example={
                "case": {
                    **_REPORT_EXAMPLE,
                    "case_id": "...",
                    "target_wallet": "...",
                    "reporter": "...",
                    "settlement_tx_id": "...",
                    "content_snapshot": "...",
                    "opened_at_epoch": 0,
                    "window_ends_at_epoch": 0,
                    "state": "open",
                    "resolved_at_epoch": None,
                    "resolution_note": None,
                },
                "settlement_tx_id": "...",
            },
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the report is still
    # decoded before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, ReportCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_REPORT_RESOURCE,
        product_write=lambda: moderation_service.open_report(
            reporter=result.payer or "",
            target_type=payload.target_type,
            target_id=payload.target_id,
            category=payload.category,
            note=payload.note,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_REPORT_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"case": _case_json(outcome, tally=None), "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_cases_list(request: Request) -> Response | dict:
    """Free: open moderation cases newest-first -- the 'jury duty' discovery surface (design doc section 5.2). No tallies here (hidden until resolution, and every case in this feed is, by construction, still open at the time it was fetched)."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    limit = _limit_param(request, default=settings.x402_social_max_results)
    if isinstance(limit, Response):
        return limit
    cases = moderation_service.list_open_cases(limit=limit)
    return {"cases": [_case_json(c, tally=None) for c in cases]}


def x402_social_case_detail(request: Request) -> Response | dict:
    """Free: one case, plus its vote tally ONLY once resolved (design doc section 5.6 Q7: tallies hidden until resolution). Lazily resolves the case first if its window has elapsed."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    case_id = query_param(request.path_params.get("case_id", ""))
    case = moderation_service.get_case(case_id) if case_id else None
    if case is None:
        return json_error_response(404, "not_found", "No case with that id")
    tally = moderation_service.vote_tally(case_id) if case.state != CASE_STATE_OPEN else None
    return {"case": _case_json(case, tally=tally)}


def x402_social_case_vote(request: Request) -> Response:
    """Paid: vote on an open moderation case (design doc section 5.2, section 5.3 step 2).

    Case existence and already-resolved state are checked BEFORE the
    payment gate (free 404) -- both are publicly readable without a
    payment (GET /cases/{id}), the same "reject before charging" split
    x402_features.vote's exists() check makes. Everything that needs the
    real payer's identity (self-vote exclusion, the registration-predates-
    the-case eligibility rule, ban status, one-vote-per-wallet) is checked
    POST-gate inside moderation_service.cast_vote, settled-then-refused if
    it trips -- those genuinely cannot be known before settlement (design
    doc section 4.1). Tallies stay hidden -- never included in this
    response either.
    """
    case_id = query_param(request.path_params.get("case_id", ""))
    case = moderation_service.get_case(case_id) if case_id else None
    if case is None or case.state != CASE_STATE_OPEN:
        return json_error_response(404, "not_found", "No open case with that id")

    if circuit_breaker.is_tripped(_CASE_VOTE_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. Try again later.",
        )

    offer = {
        "price": settings.x402_social_case_vote_price,
        "resource": _CASE_VOTE_RESOURCE,
        "resource_path": "/api/v1/x402/social/cases/{case_id}/vote",
        "description": "Vote on an open PXke x402 social moderation case ('uphold' or 'reject').",
        "extensions": describe_json_endpoint(
            body_type="json",
            input={"verdict": "uphold"},
            input_schema={
                "type": "object",
                "properties": {"verdict": {"type": "string", "enum": ["uphold", "reject"]}},
                "required": ["verdict"],
            },
            output_example={"case_id": "...", "settlement_tx_id": "..."},
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the verdict is still
    # decoded before the gate so a malformed one is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, CaseVoteRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_CASE_VOTE_RESOURCE,
        product_write=lambda: moderation_service.cast_vote(
            case_id=case_id,
            voter=result.payer or "",
            verdict=payload.verdict,
            settlement_tx_id=result.payment_txid or "",
        ),
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_CASE_VOTE_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {"case_id": case_id, "settlement_tx_id": result.payment_txid or ""}
        ),
    )


def x402_social_agent_standing(request: Request) -> Response | dict:
    """Free: one agent's full moderation standing (design doc sections 5.2/5.4) -- public, "so counterparties can check who they're dealing with." Always 200 with all-zero fields for a wallet with no history, never a 404 (zero history is itself information)."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    wallet = query_param(request.path_params.get("wallet", ""))
    if not wallet or not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )
    return {"standing": _standing_json(moderation_service.standing(wallet))}


def register_x402_social_routes(app: Router) -> None:
    """Register every x402 social route: Phase S0's identity/foundation layer, Phase S1's network (posts, comments, reactions, follows, groups, trending), and private messages (DMs, migration 122) always; Phase S2 (community moderation) ONLY when `settings.x402_social_moderation_enabled` is True.

    S2's own gate is separate from and in addition to the module-wide
    `x402_social_store != "memory"` gate this whole function sits behind in
    falcon_main.py -- a paid write against a per-process dict is invisible
    across gunicorn workers there; here, the master flag additionally keeps
    S2 entirely unregistered (clean 404, nothing charged) until the owner
    flips it deliberately, exactly the same shape as every other
    default-off product gate in this backend.
    """
    # Phase S0.
    #
    # x402-marketplace-ux-audit.md section 3.3 "network / social": `register`
    # collided with the directory's own "Register" naming (N7), `profile` PATCH
    # didn't read as "your own agent," and the leaderboard is one of three
    # unrelated paid "leaderboards" sharing no namespace (N4). Each new path
    # below is a second, direct registration against the identical old
    # handler; the old paths are never removed (section 3.5, live-mainnet
    # callers).
    app.post("/api/v1/x402/social/auth/challenge")(x402_social_auth_challenge)
    app.post("/api/v1/x402/social/auth/session")(x402_social_auth_session)
    app.post("/api/v1/x402/social/register")(x402_social_register)
    app.post("/api/v1/x402/social/agents")(x402_social_register)
    app.patch("/api/v1/x402/social/profile")(x402_social_profile_patch)
    app.patch("/api/v1/x402/social/agents/me")(x402_social_profile_patch)
    app.get("/api/v1/x402/social/agents/:wallet")(x402_social_agent_detail)
    app.get("/api/v1/x402/social/agents")(x402_social_agents_list)
    app.get("/api/v1/x402/social/agents/search")(x402_social_agent_search)
    app.get("/api/v1/x402/social/agents/leaderboard")(x402_social_agent_leaderboard)
    app.get("/api/v1/x402/trust/leaderboards/spend")(x402_social_agent_leaderboard)

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
    app.post("/api/v1/x402/social/groups/:group_id/members")(x402_social_group_join)
    app.delete("/api/v1/x402/social/groups/:group_id/membership")(x402_social_group_leave)
    # New path pairs with the existing DELETE .../members/:wallet
    # (x402_social_group_remove_member, a moderator removing someone else);
    # "me" is a literal path segment, so it never collides with that
    # template -- Falcon's compiled router matches literal siblings before
    # parameterized ones.
    app.delete("/api/v1/x402/social/groups/:group_id/members/me")(x402_social_group_leave)
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

    # Private messages (DMs, migration 122) -- free, session-authenticated,
    # registered unconditionally (not gated behind
    # x402_social_moderation_enabled -- S2 is a separate concern).
    app.post("/api/v1/x402/social/dm")(x402_social_dm_send)
    app.get("/api/v1/x402/social/dm")(x402_social_dm_conversations)
    app.get("/api/v1/x402/social/dm/:wallet")(x402_social_dm_conversation)

    # Phase S2: community moderation -- gated off by default (see this
    # function's own docstring).
    if settings.x402_social_moderation_enabled:
        app.post("/api/v1/x402/social/reports")(x402_social_report_create)
        app.get("/api/v1/x402/social/cases")(x402_social_cases_list)
        app.get("/api/v1/x402/social/cases/:case_id")(x402_social_case_detail)
        app.post("/api/v1/x402/social/cases/:case_id/vote")(x402_social_case_vote)
        app.get("/api/v1/x402/social/agents/:wallet/standing")(x402_social_agent_standing)
