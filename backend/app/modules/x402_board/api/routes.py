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
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
from app.modules.x402.promo import promo_request_params
from app.modules.x402_board.models.domain import BoardError, StoredPlacement
from app.modules.x402_board.services.board_service import BoardService, normalize_link
from app.modules.x402_board.services.rate_limit import (
    board_click_rate_limited,
    board_read_rate_limited,
)
from app.schemas import X402BoardPlacementRequest

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by both routes.
board_service = BoardService()

_PLACEMENT_EXAMPLE = {
    "link": "https://agent.example.com",
    "name": "Example Agent",
    "pitch": "Autonomous FX arbitrage agent. Live on Algorand since 2026.",
}


def _placement_json(item: StoredPlacement, *, clicks: int | None = None) -> dict:
    """Serialize a stored placement for the wire.

    `entry_id` is served because it is what a caller renews
    (POST /board/:entry_id/renew) and clicks through (GET /board/:entry_id/go).
    `clicks` is included only when the caller resolved it (the feed does, via
    one batched counter read); the placement and renewal responses omit it.
    """
    payload = {
        "entry_id": item.entry_id,
        "link": item.link,
        "name": item.name,
        "pitch": item.pitch,
        "payer": item.payer,
        "term_end_epoch": item.term_end_epoch,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }
    if clicks is not None:
        payload["clicks"] = clicks
    return payload


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
    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_board_price,
        resource="x402-board-place",
        promo_code=promo_code,
        promo_wallet=promo_wallet,
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
            # A promo redemption settles nothing, so result.payer is empty —
            # attribute the placement to the caller's own claimed wallet
            # instead (already validated in promo.attempt_promo_redemption).
            payer=result.payer or promo_wallet,
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

    if not result.is_promo:
        mark_fulfilled(result.payment_txid, resource="x402-board-place")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "placement": _placement_json(placement),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
                **({"via": "promo"} if result.is_promo else {}),
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
    # One batched counter read for the page, bounded by the clamped limit --
    # never a bump: the feed is the hot read path and stays a pure read.
    clicks = board_service.click_counts(items)
    return {"items": [_placement_json(item, clicks=clicks.get(item.entry_id, 0)) for item in items]}


def x402_board_renew(request: Request) -> Response:
    """Paid: extend an existing placement's term by one more term, owner only.

    Existence is checked BEFORE the payment gate: renewing an unknown entry
    is a free 404, not a charged one. Ownership CANNOT be checked before the
    gate -- the payer is only known once the payment has settled -- so a
    renewal by a wallet other than the placer is refused with the payment
    already taken and the tile untouched (403). This is the same accepted
    tradeoff as the directory's relist ownership check, and the 402 offer
    says so before the payer commits. Priced at the placement price: a
    renewal buys exactly one more term.

    A placement with no attributed payer (placed by an unattributable
    payment, keyed on its txid) can never pass the ownership check, so it is
    refused here, BEFORE the gate, with a 409 -- charging for it would settle
    a payment that is guaranteed to end in the 403 above. Re-placing the
    link buys a fresh tile instead.
    """
    entry_id = query_param(request.path_params.get("entry_id", ""))
    placement = board_service.get(entry_id)
    if placement is None:
        return json_error_response(404, "not_found", "No board placement with that id")
    if not placement.payer.strip():
        return json_error_response(
            409,
            "not_renewable",
            "This placement has no attributed owner wallet and cannot be renewed. "
            "Re-place the link with POST /api/v1/x402/board instead.",
        )

    term_days = settings.x402_board_term_days
    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_board_price,
        resource="x402-board-renew",
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        description=(
            f"Extend your existing PXke x402 board placement by {term_days} more days, "
            f"from the later of now and its current term end. Only the wallet that "
            f"placed the entry may renew it: a payment from any other wallet settles "
            f"but is refused and changes nothing."
        ),
        # No body and no query params: the only input is the entry id in the
        # path, which the Bazaar reads from the route template itself.
        extensions=describe_json_endpoint(
            output_example={
                "placement": {
                    **_PLACEMENT_EXAMPLE,
                    "entry_id": "0" * 64,
                    "payer": "...",
                    "term_end_epoch": 0,
                    "created_at_epoch": 0,
                    "settlement_tx_id": "...",
                },
                "settlement_tx_id": "...",
                "term_days": term_days,
            }
        ),
    )
    if result.error:
        return result.error

    try:
        renewed = board_service.renew(
            placement=placement,
            # See x402_board_place's identical fallback: a promo redemption
            # settles nothing, so result.payer is empty on a promo result.
            payer=result.payer or promo_wallet,
            settlement_tx_id=result.payment_txid or "",
        )
    except BoardError as exc:
        # The ownership refusal: payment taken, nothing changed. The
        # settlement headers are still served so the payer has their receipt.
        return Response(
            status_code=exc.http_status,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps({"error": {"code": exc.code, "message": exc.message}}),
        )

    if not result.is_promo:
        mark_fulfilled(result.payment_txid, resource="x402-board-renew")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "placement": _placement_json(renewed),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
                **({"via": "promo"} if result.is_promo else {}),
            }
        ),
    )


def x402_board_go(request: Request) -> Response:
    """Free: 302 to a live placement's link, counting the click-through, rate-limited per IP.

    The counter is bumped here and only here, so the feed read never writes.
    An unknown or expired entry is a 404: an ended term stops being advertised,
    redirects included.
    """
    if board_click_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many board click-throughs — please try again later"
        )
    entry_id = query_param(request.path_params.get("entry_id", ""))
    link = board_service.click(entry_id)
    if link is None:
        return json_error_response(404, "not_found", "No live board placement with that id")
    return Response(
        status_code=302,
        # Never cached: a cached redirect would skip the counter on every
        # repeat visit and, worse, keep redirecting after the term ends.
        # This is an open redirect to a payer-chosen link by design, so it
        # is kept out of search indexes and sends no referrer to the target:
        # the redirect must not lend our domain's ranking or reveal our
        # visitors' paths to whatever the tile links to.
        headers={
            "Location": link,
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "Referrer-Policy": "no-referrer",
        },
        description="",
    )


def x402_admin_delete_placement(request: Request) -> Response | dict:
    """Admin: remove a placement outright, without waiting out its paid term.

    For an abuse report or a link that must come down now. Same shape as the
    directory's admin delist: the entry id is a query param, and the session
    wallet is verified first -- the X-Admin-Wallet header is never trusted.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    entry_id = query_param(request.query_params.get("entry_id", ""))
    if not entry_id:
        return json_error_response(400, "invalid_request", "entry_id is required")

    deleted = board_service.delete(entry_id)
    if not deleted:
        return json_error_response(404, "not_found", "No board placement with that id")
    return {"deleted": True, "entry_id": entry_id}


def register_x402_board_routes(app: Router) -> None:
    """Register the board's paid routes (place, renew), free routes (feed, click-through) and the admin delete."""
    app.post("/api/v1/x402/board")(x402_board_place)
    app.get("/api/v1/x402/board")(x402_board_read)
    app.post("/api/v1/x402/board/:entry_id/renew")(x402_board_renew)
    app.get("/api/v1/x402/board/:entry_id/go")(x402_board_go)
    app.delete("/api/v1/admin/x402/board")(x402_admin_delete_placement)
