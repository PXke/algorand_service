"""HTTP routes for the x402 feature-request board: 2 free surfaces, 4 paid.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/features would be unreachable in production without an nginx change
this change is not authorized to deploy.

Filing a request and browsing the board are free and rate-limited per IP
(CLAUDE.md section 9). Voting and reading the ranked demand are paid, and
both obey the same two rules the board's routes do: everything that can make
the request invalid is checked BEFORE the payment gate, so nobody is ever
charged for a request that cannot succeed, and once a payment has settled the
handler never returns a 4xx.
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import promo_request_params
from app.modules.x402_features.models.domain import (
    FEATURE_STATUS_CLAIMED,
    FEATURE_STATUS_COMPLETED,
    FEATURE_STATUS_PENDING,
    ClaimSummary,
    FeatureError,
    RankedFeatureRequest,
    StoredFeatureRequest,
)
from app.modules.x402_features.services.feature_service import FeatureService
from app.modules.x402_features.services.rate_limit import (
    features_read_rate_limited,
    features_submit_rate_limited,
)
from app.schemas import X402FeatureRequestSubmission

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by all four routes.
feature_service = FeatureService()

_VOTE_RESOURCE = "x402-features-vote"
_CLAIM_RESOURCE = "x402-features-claim"
_DEMAND_RESOURCE = "x402-features-demand"
_COMPLETE_RESOURCE = "x402-features-complete"

_REQUEST_EXAMPLE = {
    "title": "Historical ASA price candles endpoint",
    "description": (
        "An endpoint returning OHLCV candles for any Algorand ASA over an "
        "arbitrary date range, so agents stop scraping block explorers."
    ),
}


def _claims_json(summary: ClaimSummary) -> dict:
    """The claim annotation both surfaces carry: how many builders declared, and who last did.

    Claims are public by design (a claim is a builder announcing themselves),
    so they sit on the FREE surface too -- they are not the demand signal.
    An empty latest claimer is served as null, never as a placeholder.
    """
    return {"claims_count": summary.count, "latest_claimer": summary.latest_claimer or None}


def _public_json(item: StoredFeatureRequest, claims: ClaimSummary, status: str) -> dict:
    """Serialize a request for the FREE browse surface.

    Existence only: the id (so a caller knows what to vote on or claim), the
    title, the description, when it was filed, the public claim annotation,
    and the lifecycle status (migration 119) -- pending/claimed/completed is
    existence-shaped information ("is this spoken for"), not the paid demand
    signal, so it sits on the free surface with the claim annotation. NO vote
    total and no submitter -- the demand signal is what the paid surface
    sells, and giving the numbers away here would leave it selling nothing.
    Keep this function and _demand_json separate rather than adding a flag:
    one boolean away from leaking the paid field is exactly the kind of
    mistake a free/paid split cannot afford.
    """
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "created_at_epoch": item.created_at_epoch,
        "status": status,
        **_claims_json(claims),
    }


def _demand_json(ranked: RankedFeatureRequest, claims: ClaimSummary, status: str) -> dict:
    """Serialize a ranked request for the PAID demand surface, vote total included.

    Requests are filed free and anonymously, so `submitter` is null -- served
    as null rather than as an empty string or a placeholder, so a builder
    reading demand is never handed a fabricated author. The demand signal
    itself (vote_total) is what this surface sells; the claim annotation and
    the lifecycle status are the same public ones the free browse carries --
    "which requests are claimed or in progress" is literally the demand
    quote this field answers.
    """
    item = ranked.request
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "submitter": item.submitter or None,
        "created_at_epoch": item.created_at_epoch,
        "vote_total": ranked.vote_total,
        "status": status,
        **_claims_json(claims),
    }


def x402_features_submit(request: Request) -> Response:
    """Free: file one anonymous feature request on the public board, rate-limited per IP.

    Filing is free because the board exists to collect "I wish an x402
    endpoint existed that..." ideas from agents, and a fee is friction against
    exactly that. Demand on a request is still a costly signal -- voting stays
    paid (see x402_features_vote). Filing buys a listing and a place in the
    demand ranking, not a commitment to build anything.

    Anonymous: there is no payment to attribute the request to and no
    self-declared wallet field, so the stored submitter is empty. The
    per-IP hourly budget is counted before the body is parsed, so malformed
    bodies burn budget rather than being a free way to probe.
    """
    if features_submit_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many feature requests filed — please try again later"
        )

    try:
        payload = serialization.decode(request.body, X402FeatureRequestSubmission)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        item = feature_service.create(title=payload.title, description=payload.description)
    except FeatureError as exc:
        # The title is already validated during decode; _clean_title runs the
        # same rule. Kept explicit so a future validation rule maps to a 4xx
        # rather than a 500.
        return json_error_from_platform(exc)

    return Response(
        status_code=201,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {"request": _public_json(item, ClaimSummary(), FEATURE_STATUS_PENDING)}
        ),
    )


def x402_features_vote(request: Request) -> Response:
    """Paid: add one unit of demand to an existing feature request.

    Existence is checked BEFORE the payment gate. A vote for an unknown
    request id is a 404 and costs nothing -- charging for it would take money
    for an increment that can never land anywhere. This is the same
    reject-before-charging rule the demand route applies to a bad limit, and
    it is why the 404 here is not a violation of "a settled payment never
    yields a 4xx": nothing has settled yet when it is returned.

    Paying again votes again. See FeatureService.vote for why this is not
    capped at one vote per wallet.

    Does NOT accept modules/x402/promo.py's promo-code bypass (2026-09-07
    security review, finding 10 -- the same pattern already found and fixed
    for x402_directory and x402_board): `voter` is stored as the identity
    behind this vote, but a promo redemption's wallet is only checked for
    SYNTACTIC validity, never proof of control (see promo.py's own
    docstring) -- a promo bypass would let anyone cast a vote "as" any
    wallet via `?promo_wallet=`, sybil-inflating demand under real wallets'
    names for free. Real payment only; see x402_features_claim/_complete for
    the identical reasoning on claiming/completing.
    """
    request_id = query_param(request.path_params.get("request_id", ""))
    if not request_id or not feature_service.exists(request_id):
        return json_error_response(404, "not_found", "No feature request with that id")

    if circuit_breaker.is_tripped(_VOTE_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    result = require_paid_request(
        request,
        price=settings.x402_features_vote_price,
        resource=_VOTE_RESOURCE,
        resource_path="/api/v1/x402/features/{request_id}/vote",
        # The payer needs to know before committing that this is additive and
        # repeatable, not a toggle they might be paying to flip twice.
        description=(
            "Cast one paid vote of demand on a PXke x402 feature request. Each "
            "settled payment adds one to that request's demand total, so the "
            "same wallet may vote again by paying again. Totals are readable "
            "at GET /api/v1/x402/features/demand."
        ),
        # No input example: this route takes no body and no query params; its
        # only input is the request id in the path, which the Bazaar reads
        # from the route template itself. body_type="json" is still required
        # because this is a POST: the query-params declaration's schema only
        # admits GET/HEAD/DELETE, so once the resource server injects
        # method=POST the facilitator's validator rejects it and the route is
        # never catalogued (see describe_json_endpoint's docstring).
        extensions=describe_json_endpoint(
            body_type="json",
            output_example={"request_id": "...", "vote_total": 1, "settlement_tx_id": "..."},
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_VOTE_RESOURCE,
        product_write=lambda: {
            "vote_total": feature_service.vote(
                request_id=request_id,
                voter=result.payer,
                settlement_tx_id=result.payment_txid or "",
            )
        },
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_VOTE_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request_id": request_id,
                "vote_total": outcome["vote_total"],
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_features_browse(request: Request) -> Response | dict:
    """Free: what has been asked for, newest first, rate-limited per IP.

    Carries no vote counts by design -- see _public_json. Its budget is
    separate from the free filing route's.
    """
    if features_read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many feature-board requests — please try again later"
        )

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_features_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = feature_service.list_recent(limit=limit)
    claims = feature_service.claim_summaries(items)
    statuses = feature_service.statuses_for(items)
    return {
        "items": [
            _public_json(
                item,
                claims.get(item.request_id, ClaimSummary()),
                statuses.get(item.request_id, FEATURE_STATUS_PENDING),
            )
            for item in items
        ]
    }


def x402_features_claim(request: Request) -> Response:
    """Paid: a builder wallet declares "I'm building this" against a request.

    Existence is checked BEFORE the payment gate -- claiming an unknown
    request is a free 404, same rule as voting. Priced at the vote price: a
    claim is one costly public statement, the same weight as one unit of
    demand. Multiple claims are allowed (see FeatureService.claim); the
    claimer is always the settled payer, never anything in a body.

    Does NOT accept the promo-code bypass, same reason as x402_features_vote
    (2026-09-07 security review, finding 10): `claimer` is a public identity
    statement ("I'm building this"), and a promo bypass would let anyone
    claim "as" any wallet via `?promo_wallet=` -- real payment only.
    """
    request_id = query_param(request.path_params.get("request_id", ""))
    if not request_id or not feature_service.exists(request_id):
        return json_error_response(404, "not_found", "No feature request with that id")

    if circuit_breaker.is_tripped(_CLAIM_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    result = require_paid_request(
        request,
        price=settings.x402_features_vote_price,
        resource=_CLAIM_RESOURCE,
        resource_path="/api/v1/x402/features/{request_id}/claim",
        description=(
            "Declare that your wallet is building a PXke x402 feature request. The "
            "claim is public: the request's claim count and your wallet as latest "
            "claimer appear on the free board and the paid demand read. Not "
            "exclusive -- other builders may claim the same request, and you may "
            "claim again."
        ),
        # No body and no query params: the only input is the request id in
        # the path, which the Bazaar reads from the route template itself.
        # body_type="json" for the same reason as x402_features_vote: a POST
        # must declare a body extension or the facilitator rejects it.
        extensions=describe_json_endpoint(
            body_type="json",
            output_example={
                "request_id": "...",
                "claims_count": 2,
                "latest_claimer": "...",
                "status": "claimed",
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_CLAIM_RESOURCE,
        product_write=lambda: {
            "summary": feature_service.claim(
                request_id=request_id,
                claimer=result.payer,
                settlement_tx_id=result.payment_txid or "",
            )
        },
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_CLAIM_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request_id": request_id,
                **_claims_json(outcome["summary"]),
                "status": FEATURE_STATUS_CLAIMED,
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_features_complete(request: Request) -> Response:
    """Paid: a past claimer self-declares a feature request completed. Never verified.

    Existence is checked BEFORE the payment gate -- same free-404 rule as
    vote/claim. Authorization (was the settled payer ever a claimer on this
    request) can only be checked AFTER settlement: there is no self-declared
    wallet field before payment, the same reason vote/claim's identity comes
    from the settled payer rather than the request body. A payer who was
    never a claimer keeps their money -- FeatureService.mark_completed raises
    FeatureError, which run_with_refund treats as a payment-kept,
    ownership-style rejection (never refunded), not a delivery failure.

    Completion is a further self-declared statement, exactly like the claim
    itself -- not verified delivery (see FeatureService.mark_completed and
    docs/x402-execution-trust-evaluation.md). A later claim on this request
    reopens it back to 'claimed' -- see FeatureService.claim.

    Does NOT accept the promo-code bypass, same reason as x402_features_vote/
    _claim (2026-09-07 security review, finding 10) -- and more sharply here:
    `claimer` is compared against this request's real past claimers to
    authorize the completion. A promo-supplied `?promo_wallet=` is checked
    for syntactic validity only, never proof of control, so it would have let
    anyone falsely mark ANY publicly-visible past claimer's work "complete"
    (or vice versa) by citing that claimer's own address, for free. Real
    payment only -- result.payer there comes from actual settlement, not a
    caller-declared string.
    """
    request_id = query_param(request.path_params.get("request_id", ""))
    if not request_id or not feature_service.exists(request_id):
        return json_error_response(404, "not_found", "No feature request with that id")

    if circuit_breaker.is_tripped(_COMPLETE_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    result = require_paid_request(
        request,
        price=settings.x402_features_complete_price,
        resource=_COMPLETE_RESOURCE,
        resource_path="/api/v1/x402/features/{request_id}/complete",
        # The payer needs to know up front this is gated on having claimed,
        # and that it is a statement, not a verification, before committing.
        description=(
            "Declare that a PXke x402 feature request is complete. Only a wallet "
            "that has claimed this request (at any point, not just the latest "
            "claimer) may do this -- other wallets are charged nothing (checked "
            "after settlement; the payment is refused, not refunded, same as an "
            "ownership conflict elsewhere in this marketplace). This is a further "
            "self-declared statement, like the claim itself -- never verified. A "
            "later claim on the same request reopens it to 'claimed'."
        ),
        # No body and no query params: the only input is the request id in
        # the path. body_type="json" for the same reason as vote/claim: a
        # POST must declare a body extension or the facilitator rejects it.
        extensions=describe_json_endpoint(
            body_type="json",
            output_example={
                "request_id": "...",
                "status": "completed",
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_COMPLETE_RESOURCE,
        product_write=lambda: feature_service.mark_completed(
            request_id=request_id, claimer=result.payer
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_COMPLETE_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request_id": request_id,
                "status": FEATURE_STATUS_COMPLETED,
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_features_demand(request: Request) -> Response:
    """Paid: feature requests ranked by demand, vote totals included.

    "Builders pay to read demand" -- roadmap item 4. This is the product: the
    aggregate of every vote every agent has paid for, ordered so the first row
    is what the market most wants built.

    An unpaid request sees the offer before `limit` is ever parsed (see
    challenge_if_unpaid) -- the same bug class as x402_directory's own
    probe-leaderboard and x402_grading's own tag leaderboard: a bare
    header-less probe with a malformed `limit` got a 400 and never saw the
    price. With a payment attached, `limit` is still parsed and validated
    before the gate as before (a non-integer is a free 400).
    """
    if circuit_breaker.is_tripped(_DEMAND_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    offer = {
        "price": settings.x402_features_demand_price,
        "resource": _DEMAND_RESOURCE,
        "description": (
            "Read the PXke x402 feature-request board ranked by paid demand, "
            "with each request's vote total — what agents have actually staked "
            "money on wanting built. The free GET /api/v1/x402/features lists "
            "the same requests without the demand signal. Supports ?preview=true "
            "(redacted, unpaid, rate-limited)."
        ),
        # A GET whose input is query params, so no body_type — the package's
        # default query-params declaration is the correct one here.
        "extensions": describe_json_endpoint(
            input={"limit": 25},
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
            output_example={
                "items": [
                    {
                        **_REQUEST_EXAMPLE,
                        "request_id": "...",
                        "submitter": None,
                        "created_at_epoch": 0,
                        "vote_total": 7,
                        "status": "claimed",
                        "claims_count": 1,
                        "latest_claimer": "...",
                    }
                ],
                "settlement_tx_id": "...",
            },
        ),
    }
    # An unpaid request sees the offer before its query string is validated
    # (see challenge_if_unpaid); with a payment attached, limit is still
    # validated before the gate so a malformed query is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_features_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        # Shape, not values: rank_by_demand is never called for a preview
        # caller -- the ORDER is derived from vote_total (the paid signal),
        # not just the numbers, so a real ranking must never leak even with
        # vote_total blanked out. One redacted exemplar row, matching the
        # free /features board's own example fields, plus a sentinel
        # vote_total (-1 -- a real one is never negative).
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {
                    "items": [
                        {
                            **_REQUEST_EXAMPLE,
                            "request_id": "<preview>",
                            "submitter": None,
                            "created_at_epoch": 0,
                            "vote_total": -1,
                            "status": FEATURE_STATUS_PENDING,
                            "claims_count": 0,
                            "latest_claimer": None,
                        }
                    ],
                    "settlement_tx_id": "<preview>",
                }
            ),
        )

    if result.is_promo:
        # A promo redemption settles nothing, so there is no payer/txid to
        # refund on failure -- run_with_refund is for a real settlement only.
        ranked = feature_service.rank_by_demand(limit=limit)
        claims = feature_service.claim_summaries([item.request for item in ranked])
        statuses = feature_service.statuses_for([item.request for item in ranked])
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps(
                {
                    "items": [
                        _demand_json(
                            item,
                            claims.get(item.request.request_id, ClaimSummary()),
                            statuses.get(item.request.request_id, FEATURE_STATUS_PENDING),
                        )
                        for item in ranked
                    ],
                    "settlement_tx_id": "",
                    "via": "promo",
                }
            ),
        )

    outcome = run_with_refund(
        result,
        resource=_DEMAND_RESOURCE,
        product_write=lambda: _demand_product_write(limit),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_DEMAND_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def _demand_product_write(limit: int) -> dict:
    """The real demand-ranking read run_with_refund wraps: rank + claim annotations.

    Returns a plain dict (never a Response), same contract as _ping_product_write.
    """
    ranked = feature_service.rank_by_demand(limit=limit)
    claims = feature_service.claim_summaries([item.request for item in ranked])
    statuses = feature_service.statuses_for([item.request for item in ranked])
    return {
        "items": [
            _demand_json(
                item,
                claims.get(item.request.request_id, ClaimSummary()),
                statuses.get(item.request.request_id, FEATURE_STATUS_PENDING),
            )
            for item in ranked
        ]
    }


def x402_admin_delete_feature_request(request: Request) -> Response | dict:
    """Admin: remove a feature request, its recency row and its claims outright.

    For spam or abuse on the free filing surface. Same shape as the
    directory's admin delist: id as a query param, session wallet verified
    first -- the X-Admin-Wallet header is never trusted. The request's vote
    counter and vote audit log are kept (see FeatureStore.delete).
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    request_id = query_param(request.query_params.get("request_id", ""))
    if not request_id:
        return json_error_response(400, "invalid_request", "request_id is required")

    deleted = feature_service.delete(request_id)
    if not deleted:
        return json_error_response(404, "not_found", "No feature request with that id")
    return {"deleted": True, "request_id": request_id}


def register_x402_features_routes(app: Router) -> None:
    """Register the feature board's two free routes (file, browse), four paid ones (vote, claim, complete, demand) and the admin delete.

    x402-marketplace-ux-audit.md section 3.3 "discover / requests" renames
    the product's paths from `/features...` to `/requests...` (the UI already
    calls it Requests). Each `/requests...` path is a second direct
    registration against the identical `/features...` handler -- the old
    paths are never removed (section 3.5, live-mainnet callers).
    """
    app.post("/api/v1/x402/features")(x402_features_submit)
    app.post("/api/v1/x402/requests")(x402_features_submit)
    app.get("/api/v1/x402/features")(x402_features_browse)
    app.get("/api/v1/x402/requests")(x402_features_browse)
    # /features/demand does not collide with /features/:request_id/vote: the
    # vote route carries a further /vote segment, so the two templates differ
    # in length and never compete for the same path.
    app.get("/api/v1/x402/features/demand")(x402_features_demand)
    app.get("/api/v1/x402/requests/ranked")(x402_features_demand)
    app.post("/api/v1/x402/features/:request_id/vote")(x402_features_vote)
    app.post("/api/v1/x402/requests/:request_id/votes")(x402_features_vote)
    app.post("/api/v1/x402/features/:request_id/claim")(x402_features_claim)
    app.post("/api/v1/x402/requests/:request_id/claims")(x402_features_claim)
    app.post("/api/v1/x402/features/:request_id/complete")(x402_features_complete)
    app.post("/api/v1/x402/requests/:request_id/completions")(x402_features_complete)
    app.delete("/api/v1/admin/x402/features")(x402_admin_delete_feature_request)
