"""HTTP routes for the x402 feature-request board: 2 free surfaces, 2 paid.

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
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
from app.modules.x402_features.models.domain import (
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


def _public_json(item: StoredFeatureRequest, claims: ClaimSummary) -> dict:
    """Serialize a request for the FREE browse surface.

    Existence only: the id (so a caller knows what to vote on or claim), the
    title, the description, when it was filed, and the public claim
    annotation. NO vote total and no submitter -- the demand signal is what
    the paid surface sells, and giving the numbers away here would leave it
    selling nothing. Keep this function and _demand_json separate rather than
    adding a flag: one boolean away from leaking the paid field is exactly
    the kind of mistake a free/paid split cannot afford.
    """
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "created_at_epoch": item.created_at_epoch,
        **_claims_json(claims),
    }


def _demand_json(ranked: RankedFeatureRequest, claims: ClaimSummary) -> dict:
    """Serialize a ranked request for the PAID demand surface, vote total included.

    Requests are filed free and anonymously, so `submitter` is null -- served
    as null rather than as an empty string or a placeholder, so a builder
    reading demand is never handed a fabricated author. The demand signal
    itself (vote_total) is what this surface sells; the claim annotation is
    the same public one the free browse carries.
    """
    item = ranked.request
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "submitter": item.submitter or None,
        "created_at_epoch": item.created_at_epoch,
        "vote_total": ranked.vote_total,
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
        description=serialization.dumps({"request": _public_json(item, ClaimSummary())}),
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
    """
    request_id = query_param(request.path_params.get("request_id", ""))
    if not request_id or not feature_service.exists(request_id):
        return json_error_response(404, "not_found", "No feature request with that id")

    result = require_paid_request(
        request,
        price=settings.x402_features_vote_price,
        resource="x402-features-vote",
        resource_path="/api/v1/x402/features/{request_id}/vote",
        # The payer needs to know before committing that this is additive and
        # repeatable, not a toggle they might be paying to flip twice.
        description=(
            "Cast one paid vote of demand on a PXke x402 feature request. Each "
            "settled payment adds one to that request's demand total, so the "
            "same wallet may vote again by paying again. Totals are readable "
            "at GET /api/v1/x402/features/demand."
        ),
        # No input declaration: this route takes no body and no query params.
        # Its only input is the request id in the path, which the Bazaar reads
        # from the route template itself.
        extensions=describe_json_endpoint(
            output_example={"request_id": "...", "vote_total": 1, "settlement_tx_id": "..."}
        ),
    )
    if result.error:
        return result.error

    vote_total = feature_service.vote(
        request_id=request_id,
        voter=result.payer or "",
        settlement_tx_id=result.payment_txid or "",
    )
    mark_fulfilled(result.payment_txid, resource="x402-features-vote")

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request_id": request_id,
                "vote_total": vote_total,
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
    return {
        "items": [_public_json(item, claims.get(item.request_id, ClaimSummary())) for item in items]
    }


def x402_features_claim(request: Request) -> Response:
    """Paid: a builder wallet declares "I'm building this" against a request.

    Existence is checked BEFORE the payment gate -- claiming an unknown
    request is a free 404, same rule as voting. Priced at the vote price: a
    claim is one costly public statement, the same weight as one unit of
    demand. Multiple claims are allowed (see FeatureService.claim); the
    claimer is always the settled payer, never anything in a body.
    """
    request_id = query_param(request.path_params.get("request_id", ""))
    if not request_id or not feature_service.exists(request_id):
        return json_error_response(404, "not_found", "No feature request with that id")

    result = require_paid_request(
        request,
        price=settings.x402_features_vote_price,
        resource="x402-features-claim",
        description=(
            "Declare that your wallet is building a PXke x402 feature request. The "
            "claim is public: the request's claim count and your wallet as latest "
            "claimer appear on the free board and the paid demand read. Not "
            "exclusive -- other builders may claim the same request, and you may "
            "claim again."
        ),
        # No body and no query params: the only input is the request id in
        # the path, which the Bazaar reads from the route template itself.
        extensions=describe_json_endpoint(
            output_example={
                "request_id": "...",
                "claims_count": 2,
                "latest_claimer": "...",
                "settlement_tx_id": "...",
            }
        ),
    )
    if result.error:
        return result.error

    summary = feature_service.claim(
        request_id=request_id,
        claimer=result.payer or "",
        settlement_tx_id=result.payment_txid or "",
    )
    mark_fulfilled(result.payment_txid, resource="x402-features-claim")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request_id": request_id,
                **_claims_json(summary),
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_features_demand(request: Request) -> Response:
    """Paid: feature requests ranked by demand, vote totals included.

    "Builders pay to read demand" -- roadmap item 4. This is the product: the
    aggregate of every vote every agent has paid for, ordered so the first row
    is what the market most wants built.

    The limit is parsed and validated BEFORE the payment gate, so a caller who
    sends a bad one is not charged for the 400.
    """
    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_features_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    result = require_paid_request(
        request,
        price=settings.x402_features_demand_price,
        resource="x402-features-demand",
        description=(
            "Read the PXke x402 feature-request board ranked by paid demand, "
            "with each request's vote total — what agents have actually staked "
            "money on wanting built. The free GET /api/v1/x402/features lists "
            "the same requests without the demand signal."
        ),
        # A GET whose input is query params, so no body_type — the package's
        # default query-params declaration is the correct one here.
        extensions=describe_json_endpoint(
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
                        "claims_count": 1,
                        "latest_claimer": "...",
                    }
                ],
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    ranked = feature_service.rank_by_demand(limit=limit)
    claims = feature_service.claim_summaries([item.request for item in ranked])
    mark_fulfilled(result.payment_txid, resource="x402-features-demand")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "items": [
                    _demand_json(item, claims.get(item.request.request_id, ClaimSummary()))
                    for item in ranked
                ],
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


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
    """Register the feature board's two free routes (file, browse), three paid ones (vote, claim, demand) and the admin delete."""
    app.post("/api/v1/x402/features")(x402_features_submit)
    app.get("/api/v1/x402/features")(x402_features_browse)
    # /features/demand does not collide with /features/:request_id/vote: the
    # vote route carries a further /vote segment, so the two templates differ
    # in length and never compete for the same path.
    app.get("/api/v1/x402/features/demand")(x402_features_demand)
    app.post("/api/v1/x402/features/:request_id/vote")(x402_features_vote)
    app.post("/api/v1/x402/features/:request_id/claim")(x402_features_claim)
    app.delete("/api/v1/admin/x402/features")(x402_admin_delete_feature_request)
