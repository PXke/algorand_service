"""HTTP routes for the x402 feature-request board: 3 paid surfaces, 1 free.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/features would be unreachable in production without an nginx change
this change is not authorized to deploy.

Every paid route here obeys the same two rules the board's does: everything
that can make the request invalid is checked BEFORE the payment gate, so
nobody is ever charged for a request that cannot succeed, and once a payment
has settled the handler never returns a 4xx.
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import require_paid_request
from app.modules.x402_features.models.domain import (
    FeatureError,
    RankedFeatureRequest,
    StoredFeatureRequest,
)
from app.modules.x402_features.services.feature_service import FeatureService
from app.modules.x402_features.services.rate_limit import features_read_rate_limited
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


def _public_json(item: StoredFeatureRequest) -> dict:
    """Serialize a request for the FREE browse surface.

    Existence only: the id (so a caller knows what to vote on), the title, the
    description and when it was filed. NO vote total and no submitter -- the
    demand signal is what the paid surface sells, and giving the numbers away
    here would leave it selling nothing. Keep this function and
    _demand_json separate rather than adding a flag: one boolean away from
    leaking the paid field is exactly the kind of mistake a free/paid split
    cannot afford.
    """
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "created_at_epoch": item.created_at_epoch,
    }


def _demand_json(ranked: RankedFeatureRequest) -> dict:
    """Serialize a ranked request for the PAID demand surface, vote total included."""
    item = ranked.request
    return {
        "request_id": item.request_id,
        "title": item.title,
        "description": item.description,
        "submitter": item.submitter,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
        "vote_total": ranked.vote_total,
    }


def x402_features_submit(request: Request) -> Response:
    """Paid: file one feature request on the public board.

    The body is parsed and validated BEFORE the payment gate runs, so a
    malformed request is rejected with a 400 without anyone being charged for
    it. Nothing is written before the payment settles, and once it has settled
    the request is stored and returned.
    """
    try:
        payload = serialization.decode(request.body, X402FeatureRequestSubmission)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    result = require_paid_request(
        request,
        price=settings.x402_features_request_price,
        resource="x402-features-submit",
        # Reaches the payer as the 402's resource.description, before they
        # commit — it has to say that filing is not building, or a payer could
        # reasonably read the fee as buying the endpoint itself.
        description=(
            "File one feature request on the public PXke x402 feature-request "
            "board. Listed immediately at GET /api/v1/x402/features; agents "
            "signal demand for it by paying POST "
            "/api/v1/x402/features/{request_id}/vote. Filing a request buys it "
            "a listing and a place in the demand ranking, not a commitment to "
            "build it."
        ),
        extensions=describe_json_endpoint(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one, which would describe this route's input
            # incorrectly.
            body_type="json",
            input=_REQUEST_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 120},
                    "description": {"type": "string", "maxLength": 2000},
                },
                "required": ["title"],
            },
            output_example={
                "request": {**_REQUEST_EXAMPLE, "request_id": "...", "created_at_epoch": 0},
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    try:
        item = feature_service.create(
            title=payload.title,
            description=payload.description,
            submitter=result.payer or "",
            settlement_tx_id=result.payment_txid or "",
        )
    except FeatureError as exc:
        # Unreachable in practice — the title is validated during decode and
        # the only other raiser is _clean_title, which runs on the same input.
        # Kept explicit so a future validation rule cannot silently 500 a
        # request whose payment has already been taken.
        return json_error_from_platform(exc)

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "request": _public_json(item),
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def x402_features_vote(request: Request) -> Response:
    """Paid: add one unit of demand to an existing feature request.

    Existence is checked BEFORE the payment gate. A vote for an unknown
    request id is a 404 and costs nothing -- charging for it would take money
    for an increment that can never land anywhere. This is the same
    reject-before-charging rule the submit route applies to a malformed body,
    and it is why the 404 here is not a violation of "a settled payment never
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

    Carries no vote counts by design -- see _public_json.
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
    return {"items": [_public_json(item) for item in items]}


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
                        "submitter": "...",
                        "created_at_epoch": 0,
                        "vote_total": 7,
                    }
                ],
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    ranked = feature_service.rank_by_demand(limit=limit)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "items": [_demand_json(item) for item in ranked],
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
    )


def register_x402_features_routes(app: Router) -> None:
    """Register the feature board's three paid routes and its free browse route."""
    app.post("/api/v1/x402/features")(x402_features_submit)
    app.get("/api/v1/x402/features")(x402_features_browse)
    # /features/demand does not collide with /features/:request_id/vote: the
    # vote route carries a further /vote segment, so the two templates differ
    # in length and never compete for the same path.
    app.get("/api/v1/x402/features/demand")(x402_features_demand)
    app.post("/api/v1/x402/features/:request_id/vote")(x402_features_vote)
