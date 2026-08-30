"""HTTP routes for the x402 paid visibility board: paid placement, free feed.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/board would be unreachable in production without an nginx change
this change is not authorized to deploy.
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import require_paid_request
from app.modules.x402_board.models.domain import BoardError, StoredPlacement
from app.modules.x402_board.services.board_service import BoardService, normalize_link
from app.modules.x402_board.services.rate_limit import board_read_rate_limited
from app.schemas import X402BoardPlacementRequest

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by both routes.
board_service = BoardService()

_PLACEMENT_EXAMPLE = {
    "link": "https://agent.example.com",
    "name": "Example Agent",
    "pitch": "Autonomous FX arbitrage agent. Live on Algorand since 2026.",
}


def _placement_json(item: StoredPlacement) -> dict:
    """Serialize a stored placement for the wire."""
    return {
        "link": item.link,
        "name": item.name,
        "pitch": item.pitch,
        "payer": item.payer,
        "term_end_epoch": item.term_end_epoch,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }


def x402_board_place(request: Request) -> Response:
    """Paid: place one link and pitch on the public visibility board for a fixed term.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and range-checked, and the link is normalized -- which
    is where scheme and host are enforced -- both 400s. The schema's length
    bound alone does NOT establish that a link is placeable, so normalizing
    after the gate would charge for `ftp://example.com` and then reject it.

    Nothing is written before the payment settles, and once it has settled the
    placement is stored and returned -- a settled payment never yields a 4xx.
    """
    try:
        payload = serialization.decode(request.body, X402BoardPlacementRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        normalized_link = normalize_link(payload.link)
    except BoardError as exc:
        return json_error_from_platform(exc)

    term_days = settings.x402_board_term_days
    result = require_paid_request(
        request,
        price=settings.x402_board_price,
        resource="x402-board-place",
        # Reaches the payer as the 402's resource.description, before they
        # commit — the term length is not derivable from the price alone.
        description=(
            f"Place one link and pitch on the public PXke x402 visibility board "
            f"for {term_days} days. Visible immediately at GET /api/v1/x402/board."
        ),
        extensions=describe_json_endpoint(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one, which would describe this route's input
            # incorrectly.
            body_type="json",
            input=_PLACEMENT_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "link": {"type": "string", "maxLength": 2048},
                    "name": {"type": "string", "maxLength": 80},
                    "pitch": {"type": "string", "maxLength": 280},
                },
                "required": ["link"],
            },
            output_example={
                "placement": {**_PLACEMENT_EXAMPLE, "term_end_epoch": 0},
                "settlement_tx_id": "...",
                "term_days": term_days,
            },
        ),
    )
    if result.error:
        return result.error

    try:
        placement = board_service.create(
            normalized_link=normalized_link,
            name=payload.name,
            pitch=payload.pitch,
            payer=result.payer or "",
            settlement_tx_id=result.payment_txid or "",
        )
    except BoardError as exc:
        # No currently-reachable path raises here: the link was normalized
        # above, before the gate, and create()'s only raiser is its guard
        # against an empty normalized link, which normalize_link cannot
        # return. Kept explicit so that if a future rule DOES raise on this
        # side of the gate it maps to its own status instead of a bare 500 --
        # but note that reaching this line at all would mean a settled payment
        # ending in a 4xx, so a new rule belongs before the gate, not after it.
        return json_error_from_platform(exc)

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "placement": _placement_json(placement),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
            }
        ),
    )


def x402_board_read(request: Request) -> Response | dict:
    """Free: the board's live placements, newest first, rate-limited per IP."""
    if board_read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many board requests — please try again later"
        )

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_board_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = board_service.list_active(limit=limit)
    return {"items": [_placement_json(item) for item in items]}


def register_x402_board_routes(app: Router) -> None:
    """Register the paid board-placement route and the free board-read route."""
    app.post("/api/v1/x402/board")(x402_board_place)
    app.get("/api/v1/x402/board")(x402_board_read)
