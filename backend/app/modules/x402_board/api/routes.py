"""HTTP routes for the x402 paid visibility board: paid placement, free feed.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/board would be unreachable in production without an nginx change
this change is not authorized to deploy.

None of the three routes that check a payer's identity against a placement's
own owner (x402_board_place, x402_board_renew, and the click-analytics read
x402_board_click_history added 2026-09-07) accept modules/x402/promo.py's
promo-code bypass (deliberately -- none of them pass promo_code/promo_wallet
into require_paid_request). Found and closed 2026-09-04 for the first two,
the same pattern already found and fixed in x402_social 2026-09-03:
promo.py's own docstring says a promo redemption's wallet is checked for
SYNTACTIC validity only ("a successful redemption is not proof the caller
controls that wallet") -- fine for a route where payer is just payment
attribution, but all three routes here feed the payer straight in as the
wallet that OWNS the placement (create's attribution, renew's and
click_history's identical `attributed != placement.payer` check), so a promo
bypass would have let anyone place "as" any wallet via `?promo_wallet=`, or
free-renew/free-read (or otherwise probe the ownership check of) a placement
they do not own by guessing/reading its public payer address. There is no
cheap fix that keeps promo working here short of the same signed-challenge
proof x402_social flagged needing (a real design task, not done) -- so promo
is off for all three routes until that exists, full stop.
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.core.request_headers import header_value
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402_board.models.domain import BOARD_CATEGORIES, BoardError, StoredPlacement
from app.modules.x402_board.services.board_service import (
    BoardService,
    normalize_link,
    validate_category,
)
from app.modules.x402_board.services.rate_limit import (
    board_click_rate_limited,
    board_read_rate_limited,
)
from app.schemas import X402BoardPlacementRequest

# The three resources this module's require_paid_request calls charge for --
# named once so the circuit-breaker pre-check and the gate/mark_fulfilled
# calls can never drift apart (migration 102's auto-refund retrofit).
_RESOURCE_PLACE = "x402-board-place"
# Repurposed 2026-09-06 from a paid-renewal-to-survive resource id into the
# new boost resource id (the directory's identical change, see
# x402_directory/api/routes.py's own module docstring) -- the Python
# constant/function names stay `_RESOURCE_RENEW`/`x402_board_renew` since
# the ownership-check logic is unchanged, just extended in place.
_RESOURCE_RENEW = "x402-board-boost"
# Owner-only click-analytics read (migration 120). See
# settings.x402_board_click_history_price's own comment for the pricing
# reasoning and app/core/config.py's board section for the day/result caps.
_RESOURCE_CLICK_HISTORY = "x402-board-click-history"

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
    (POST /board/:entry_id/renew), clicks through (GET /board/:entry_id/go)
    and reads its own click analytics for (GET /board/:entry_id/clicks).
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
        # Priority-placement window (migration 114): 0 when not boosted,
        # else the epoch until which this placement sorts ahead of every
        # non-boosted one -- see BoardService.renew().
        "boosted_until_epoch": item.boosted_until_epoch,
        # One of BOARD_CATEGORIES (migration 120), "other" for a pre-120 tile.
        "category": item.category,
    }
    if clicks is not None:
        payload["clicks"] = clicks
    return payload


def x402_board_place(request: Request) -> Response:
    """Paid: place one link and pitch on the public visibility board for a fixed term.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and range-checked, the link is normalized -- which is
    where scheme and host are enforced -- and the category is validated
    against BOARD_CATEGORIES -- all 400s. The schema's length bound alone does
    NOT establish that a link is placeable or a category real, so checking
    either after the gate would charge for e.g. `ftp://example.com` or
    `category: "nonsense"` and then reject it.

    Nothing is written before the payment settles, and once it has settled the
    placement is stored and returned -- a settled payment never yields a 4xx.

    Auto-refund (migration 102): once the payment settles, the write goes
    through run_with_refund -- a product-write failure refunds the payer
    from the dedicated refund wallet instead of leaving a bare 500 for an
    operator to reconcile by hand. `circuit_breaker.is_tripped` is checked
    first, before the payment gate, so a resource with too many recent
    refund-triggering failures is refused before anyone is charged again.
    No promo branch here -- see this module's own docstring for why.
    """
    if circuit_breaker.is_tripped(_RESOURCE_PLACE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    term_days = settings.x402_board_term_days
    offer = {
        "price": settings.x402_board_price,
        "resource": _RESOURCE_PLACE,
        # Reaches the payer as the 402's resource.description, before they
        # commit — the term length is not derivable from the price alone.
        "description": (
            f"Place one link and pitch on the public PXke x402 visibility board "
            f"for {term_days} days. Visible immediately at GET /api/v1/x402/board."
        ),
        "extensions": describe_json_endpoint(
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
                    "category": {"type": "string", "enum": list(BOARD_CATEGORIES)},
                },
                "required": ["link"],
            },
            output_example={
                "placement": {**_PLACEMENT_EXAMPLE, "term_end_epoch": 0},
                "settlement_tx_id": "...",
                "term_days": term_days,
            },
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, every check below still
    # runs before the gate so a doomed request is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, X402BoardPlacementRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        normalized_link = normalize_link(payload.link)
        category = validate_category(payload.category)
    except BoardError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_PLACE,
        # No currently-reachable BoardError here: the link and category were
        # both validated above, before the gate, and create()'s only raiser
        # is its guard against an empty normalized link, which normalize_link
        # cannot return. If a future rule DOES raise on this side of the
        # gate, it is now refunded rather than mapped to a bespoke 4xx -- the
        # payer gets their money back for a write that never happened, which
        # is the more correct outcome for an unexpected failure after a real
        # settlement than any specific error code would be.
        product_write=lambda: {
            "placement": _placement_json(
                board_service.create(
                    normalized_link=normalized_link,
                    name=payload.name,
                    pitch=payload.pitch,
                    payer=result.payer,
                    settlement_tx_id=result.payment_txid or "",
                    category=category,
                )
            ),
            "term_days": term_days,
        },
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_PLACE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def x402_board_read(request: Request) -> Response | dict:
    """Free: the board's live placements, newest first, rate-limited per IP; optional `?category=`."""
    if board_read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many board requests — please try again later"
        )

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_board_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    raw_category = query_param(request.query_params.get("category", ""))
    category: str | None = None
    if raw_category:
        try:
            category = validate_category(raw_category)
        except BoardError as exc:
            return json_error_from_platform(exc)

    items = board_service.list_active(limit=limit, category=category)
    # One batched counter read for the page, bounded by the clamped limit --
    # never a bump: the feed is the hot read path and stays a pure read.
    clicks = board_service.click_counts(items)
    return {"items": [_placement_json(item, clicks=clicks.get(item.entry_id, 0)) for item in items]}


def x402_board_renew(request: Request) -> Response:
    """Paid: boost an existing placement to the top of the board, owner only.

    Repurposed 2026-09-06 (owner decision, the directory's identical pricing-
    model change -- see x402_directory/api/routes.py's own module docstring
    and BoardService.renew() for the full reasoning): this used to extend
    the placement's term_end; it now buys priority placement instead
    (boosted_until_epoch) and never touches term_end. The board is not
    probed, so a placement's survival is unaffected either way -- it still
    simply expires after settings.x402_board_term_days with no keep-alive.

    Existence is checked BEFORE the payment gate: boosting an unknown entry
    is a free 404, not a charged one. Ownership CANNOT be checked before the
    gate -- the payer is only known once the payment has settled -- so a
    boost by a wallet other than the placer is refused with the payment
    already taken and the tile untouched (403). This is the same accepted
    tradeoff as the directory's relist ownership check, and the 402 offer
    says so before the payer commits. Priced at the board's own boost price:
    a boost buys exactly one more boost window.

    A placement with no attributed payer (placed by an unattributable
    payment, keyed on its txid) can never pass the ownership check, so it is
    refused here, BEFORE the gate, with a 409 -- charging for it would settle
    a payment that is guaranteed to end in the 403 above. Re-placing the
    link buys a fresh tile instead.

    Auto-refund (migration 102) applies here ONLY to a genuinely unexpected
    write failure (the store.upsert inside board_service.renew), NOT to the
    ownership rejection above -- that 403 is a deliberate, payment-keeping
    outcome by design (the settlement row stays fulfilled=False forever on
    purpose, the same accepted cost as before this retrofit), and refunding
    it would silently undo the intended disincentive against boosting
    someone else's tile. Since run_with_refund refunds on ANY exception, the
    ownership check is re-evaluated here, before ever calling it, so
    board_service.renew()'s own BoardError raise (same condition, kept in
    sync) is never actually reached on this path. `circuit_breaker.
    is_tripped` is checked first, before the payment gate.
    """
    if circuit_breaker.is_tripped(_RESOURCE_RENEW):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    entry_id = query_param(request.path_params.get("entry_id", ""))
    placement = board_service.get(entry_id)
    if placement is None:
        return json_error_response(404, "not_found", "No board placement with that id")
    if not placement.payer.strip():
        return json_error_response(
            409,
            "not_renewable",
            "This placement has no attributed owner wallet and cannot be boosted. "
            "Re-place the link with POST /api/v1/x402/board instead.",
        )

    boost_days = settings.x402_board_boost_days
    result = require_paid_request(
        request,
        price=settings.x402_board_boost_price,
        resource=_RESOURCE_RENEW,
        resource_path="/api/v1/x402/board/{entry_id}/renew",
        description=(
            f"Boost your existing PXke x402 board placement to the top of the board for "
            f"{boost_days} days, from the later of now and its current boost end. "
            f"Nothing else about the placement changes, including its term. Only the "
            f"wallet that placed the entry may boost it: a payment from any other "
            f"wallet settles but is refused and changes nothing."
        ),
        # No body and no query params: the only input is the entry id in the
        # path, which the Bazaar reads from the route template itself.
        # body_type="json" because this is a POST: a query-params declaration
        # fails the facilitator's schema validation for any body method and
        # the route is never catalogued (see describe_json_endpoint).
        extensions=describe_json_endpoint(
            body_type="json",
            output_example={
                "placement": {
                    **_PLACEMENT_EXAMPLE,
                    "entry_id": "0" * 64,
                    "payer": "...",
                    "term_end_epoch": 0,
                    "created_at_epoch": 0,
                    "settlement_tx_id": "...",
                    "boosted_until_epoch": 0,
                },
                "settlement_tx_id": "...",
                "boost_days": boost_days,
            },
        ),
    )
    if result.error:
        return result.error

    # Same condition as board_service.renew()'s own BoardError raise, kept
    # in sync deliberately -- see this function's docstring for why this is
    # re-checked here rather than letting run_with_refund's uniform "any
    # exception refunds" contract swallow the ownership rejection too.
    attributed = (result.payer or "").strip()
    if not attributed or attributed != placement.payer:
        return Response(
            status_code=403,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps(
                {
                    "error": {
                        "code": "placement_owned_by_another_payer",
                        "message": (
                            "Only the wallet that placed this entry may boost it. "
                            "Payment has settled but the existing placement was not "
                            "changed."
                        ),
                    }
                }
            ),
        )

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_RENEW,
        product_write=lambda: {
            "placement": _placement_json(
                board_service.renew(
                    placement=placement,
                    payer=result.payer,
                    settlement_tx_id=result.payment_txid or "",
                )
            ),
            "boost_days": boost_days,
        },
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_RENEW)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def x402_board_go(request: Request) -> Response:
    """Free: 302 to a live placement's link, counting the click-through, rate-limited per IP.

    The counter is bumped here and only here, so the feed read never writes.
    An unknown or expired entry is a 404: an ended term stops being advertised,
    redirects included.

    The click-analytics event log (migration 120, GET .../clicks) is written
    here too, from the same request: this is the ONLY place a click is ever
    observed server-side, so it is also the only place that can capture the
    incoming Referer header (this backend's own outbound response sends none
    on the redirect itself -- see this route's own no-referrer headers below
    -- but the caller's request TO this route can carry one, from whatever
    page linked here).
    """
    if board_click_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many board click-throughs — please try again later"
        )
    entry_id = query_param(request.path_params.get("entry_id", ""))
    referrer = header_value(request.headers, "referer", "referrer")
    link = board_service.click(entry_id, referrer=referrer)
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


def _parse_click_history_days(request: Request) -> int | Response:
    """The caller's `days`, clamped to [1, x402_board_click_history_max_days]; a 400 for a non-integer."""
    raw_days = query_param(request.query_params.get("days", ""))
    try:
        requested = int(raw_days) if raw_days else settings.x402_board_click_history_max_days
    except ValueError:
        return json_error_response(400, "invalid_request", "days must be an integer")
    return max(1, min(requested, settings.x402_board_click_history_max_days))


def _click_history_product(
    entry_id: str, *, days: int, payer: str, placement_payer: str
) -> dict[str, object]:
    """The route's product_write: verify ownership, then read and return the analytics.

    Ownership can only be checked AFTER settlement (the payer is not known
    before then) -- a mismatch is a deliberate, payment-keeping BoardError,
    same shape as BoardService.renew()'s own ownership check, mapped by
    run_with_refund straight to a 403 with no refund. A placement that
    vanished between the pre-gate existence check and this call (e.g. an
    admin delete racing the payment) is NOT the payer's fault, so that raises
    a plain exception instead -- run_with_refund refunds it like any other
    unexpected post-settlement failure.
    """
    attributed = payer.strip()
    if not attributed or attributed != placement_payer:
        raise BoardError(
            "placement_owned_by_another_payer",
            "Only the wallet that placed this entry may read its click analytics. Payment "
            "has settled but no data was returned.",
            http_status=403,
        )
    history = board_service.click_history(
        entry_id, days=days, limit=settings.x402_board_click_history_max_results
    )
    if history is None:
        raise RuntimeError(f"board placement {entry_id} vanished between the gate and the read")
    return history


def x402_board_click_history(request: Request) -> Response:
    """Paid, owner-only: click activity OVER TIME for one of the caller's own placements.

    Daily click counts plus a coarse (hostname-only) referrer breakdown over
    up to the last `?days=` (capped at x402_board_click_history_max_days),
    aggregated from up to x402_board_click_history_max_results raw click
    events (migration 120) -- see BoardService.click_history's own docstring
    for the response shape and the `capped` honesty flag.

    PAID and owner-only, unlike the free lifetime `clicks` total already
    served on every GET /board item: that single number is already public on
    the free feed, so re-charging for it would add nothing, but the per-day
    breakdown and referrer data are NEW information beyond what the free feed
    publishes, and they are about ONE payer's own tile, not a public trust
    signal the way the directory's free probe history is (that data describes
    a LISTED ENDPOINT'S reachability, useful to any prospective caller of it;
    this describes one advertiser's own traffic, useful only to that
    advertiser). Priced at the small anti-spam-floor rate, not cost-recovery:
    see settings.x402_board_click_history_price's own comment.

    Existence is checked BEFORE the payment gate: reading analytics for an
    unknown entry is a free 404. So is a placement with no attributed payer
    wallet (409) -- it can never pass the ownership check below, so charging
    for it would settle a payment guaranteed to end in that 403, the same
    accepted shape as x402_board_renew's own ownership-refused-before-the-gate
    cases. Ownership itself is only knowable AFTER settlement, so it is
    checked inside `_click_history_product`, run through `run_with_refund` --
    see that function's own docstring.

    No promo branch here, deliberately, same reason as x402_board_place and
    x402_board_renew (see this module's own docstring): `payer` is compared
    against the placement's owner, and promo.py's wallet param is checked for
    syntactic validity only, never proof of control.
    """
    if circuit_breaker.is_tripped(_RESOURCE_CLICK_HISTORY):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    entry_id = query_param(request.path_params.get("entry_id", ""))
    placement = board_service.get(entry_id)
    if placement is None:
        return json_error_response(404, "not_found", "No board placement with that id")
    if not placement.payer.strip():
        return json_error_response(
            409,
            "not_readable",
            "This placement has no attributed owner wallet, so its click analytics can "
            "never be proven owned by any payer.",
        )

    days = _parse_click_history_days(request)
    if isinstance(days, Response):
        return days

    max_days = settings.x402_board_click_history_max_days
    result = require_paid_request(
        request,
        price=settings.x402_board_click_history_price,
        resource=_RESOURCE_CLICK_HISTORY,
        resource_path="/api/v1/x402/board/{entry_id}/clicks",
        description=(
            f"Click analytics for YOUR OWN board placement: daily click counts and a coarse "
            f"(hostname-only) referrer breakdown over up to the last ?days= (capped at "
            f"{max_days}), from up to {settings.x402_board_click_history_max_results} "
            f"underlying click events. Only the wallet that placed this entry may read it -- "
            f"a payment from any other wallet settles but is refused (403)."
        ),
        # No body and no query params required: `days` is optional, the only
        # required input is the entry id in the path, same shape as
        # x402_board_renew's own body_type="json" reasoning (a query-params
        # declaration fails the facilitator's schema validation for a route
        # whose method here is GET with only a path param and an optional
        # query param, so this follows the uptime history route's own GET
        # shape instead -- no body_type at all).
        extensions=describe_json_endpoint(
            output_example={
                "entry_id": "0" * 64,
                "days": 14,
                "total_clicks_in_window": 12,
                "capped": False,
                "daily": [{"date": "2026-09-01", "clicks": 2}],
                "top_referrers": [{"referrer": "example.com", "clicks": 5}],
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_CLICK_HISTORY,
        product_write=lambda: _click_history_product(
            entry_id, days=days, payer=result.payer, placement_payer=placement.payer
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_CLICK_HISTORY)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
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
    """Register the board's paid routes (place, renew, owner click analytics), free routes (feed, click-through) and the admin delete.

    x402-marketplace-ux-audit.md section 3.3 "discover / board": each
    `/board/placements...` path below is a second direct registration
    against the identical handler as its `/board...` sibling -- the old
    paths are never removed (section 3.5, live-mainnet callers).
    """
    app.post("/api/v1/x402/board")(x402_board_place)
    app.post("/api/v1/x402/board/placements")(x402_board_place)
    app.get("/api/v1/x402/board")(x402_board_read)
    app.get("/api/v1/x402/board/placements")(x402_board_read)
    app.post("/api/v1/x402/board/:entry_id/renew")(x402_board_renew)
    app.post("/api/v1/x402/board/placements/:entry_id/boost")(x402_board_renew)
    app.get("/api/v1/x402/board/:entry_id/go")(x402_board_go)
    app.get("/api/v1/x402/board/placements/:entry_id/go")(x402_board_go)
    app.get("/api/v1/x402/board/:entry_id/clicks")(x402_board_click_history)
    app.get("/api/v1/x402/board/placements/:entry_id/clicks")(x402_board_click_history)
    app.delete("/api/v1/admin/x402/board")(x402_admin_delete_placement)
