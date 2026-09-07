"""HTTP route for the x402 catalog: free, rate-limited per IP.

Served at /api/v1/x402. A /.well-known/x402 alias (verbatim copy of this
same document) and an /openapi.json are also served, at the top level rather
than under /api/ -- see app.modules.x402_wellknown, registered right after
this module in falcon_main.py, and the two matching `location` blocks it
needed on the API host's nginx server block
(deploy/nginx/algorand-platform.conf), which otherwise proxies only
`location ^~ /api/` and `/health/ready` and answers everything else with 404.
"""

from __future__ import annotations

import time
from typing import Any

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import (
    PromoError,
    create_promo_code,
    deactivate_promo_code,
    list_promo_codes,
    promo_request_params,
)
from app.modules.x402_catalog.services.catalog import (
    CATALOG_PATH,
    build_catalog,
    recent_settlements_json,
)
from app.modules.x402_catalog.services.rate_limit import (
    catalog_rate_limited,
    settlements_rate_limited,
)
from app.schemas import X402PromoCreateRequest

_RESOURCE = "x402-ping"


def x402_catalog(request: Request) -> Response | dict:
    """Free: the machine-readable index of every live x402 route, rate-limited per IP."""
    if catalog_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many catalog requests — please try again later"
        )
    return build_catalog()


def x402_recent_settlements(request: Request) -> Response | dict:
    """Free: real (non-operator) settlements across every product, newest first, rate-limited."""
    if settlements_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many settlement-feed requests — please try again later"
        )
    return recent_settlements_json()


def x402_ping(request: Request) -> Response:
    """Paid at the marketplace's lowest price: proves a client's signing pipeline works end to end.

    Nothing to build or list -- the point is entirely the round trip (402,
    build+sign a real payment, settle, get a response) at the smallest amount
    the facilitator will actually take, before a new integration risks money
    on a real product. The response carries no product data, just a receipt.

    Reference wiring for the shared preview + promo mechanisms
    (modules/x402/preview.py, modules/x402/promo.py) that every future paid
    route copies:

    * `?preview=true` bypasses the gate entirely (rate-limited, no
      facilitator call, nothing settled) and gets a REDACTED response --
      same shape, `settlement_tx_id` replaced with the literal string
      "<preview>" and `served_at_epoch` zeroed, since neither is real.
    * `?promo=CODE&promo_wallet=ADDRESS` bypasses payment for a bounded
      number of uses and, on success, gets the REAL response (promo bypasses
      payment, not product quality) with `settlement_tx_id` empty (nothing
      settled) and an extra `via: "promo"` field so nothing downstream can
      mistake it for a real payment.

    Also the reference wiring for the auto-refund opt-in (modules/x402/
    paid_request.run_with_refund, migration 102): the actual response body
    is built by `_ping_product_write` below and run through
    `run_with_refund` rather than called directly, so a route that DOES have
    a real product write to protect (unlike ping, which has none) can copy
    this exact shape. `circuit_breaker.is_tripped` is checked first, before
    the payment gate, so a resource with too many recent refund-triggering
    failures is refused before anyone is charged again.
    """
    if circuit_breaker.is_tripped(_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_ping_price,
        resource=_RESOURCE,
        description=(
            "Integration test: proves your x402 client can build, sign and settle a real "
            "payment against this marketplace's facilitator setup at the lowest price we "
            "offer, before you risk money on an actual product. Try it free first with "
            "?preview=true, a redacted, unpaid, rate-limited dry run of the same response "
            "shape."
        ),
        extensions=describe_json_endpoint(
            input=None,
            output_example={"pong": True, "settlement_tx_id": "...", "served_at_epoch": 0},
        ),
        preview=preview_requested(request),
        promo_code=promo_code,
        promo_wallet=promo_wallet,
    )
    if result.error:
        return result.error

    if result.is_preview:
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {"pong": True, "settlement_tx_id": "<preview>", "served_at_epoch": 0}
            ),
        )

    if result.is_promo:
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {
                    "pong": True,
                    "settlement_tx_id": "",
                    "served_at_epoch": int(time.time()),
                    "via": "promo",
                }
            ),
        )

    outcome = run_with_refund(
        result, resource=_RESOURCE, product_write=_ping_product_write, request=request
    )
    if isinstance(outcome, Response):
        # run_with_refund's own failure-path Response (refunded, or refund
        # pending) -- return it directly, and do NOT call mark_fulfilled,
        # same "nothing to mark" reasoning as the preview/promo branches
        # above. A SUCCESSFUL product_write() never returns a Response
        # itself (see _ping_product_write's own docstring for why that
        # distinction matters), so this isinstance check is unambiguous.
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def _ping_product_write() -> dict[str, Any]:
    """The (trivial) product write ping "sells": a receipt of the round trip.

    Returns the raw payload dict, NOT a Response -- run_with_refund's caller
    (x402_ping above) distinguishes "product_write succeeded" from "it
    failed and run_with_refund built its own error Response" purely by
    checking isinstance(outcome, Response), so a successful product_write
    must never itself return a Response or that check would be ambiguous.
    Extracted to its own function purely so run_with_refund has something to
    wrap -- ping has no real product to fail, but every other paid route
    that adopts this same wiring has a genuine product_write that can raise,
    and this is the shape that follow-up pass copies.
    """
    return {"pong": True, "served_at_epoch": int(time.time())}


def x402_admin_list_promo(request: Request) -> Response | dict:
    """Admin: every promo code with its live remaining-use count.

    There is no payment-path consumer of this route -- it exists purely for
    the admin promo-codes tab, so it stays a plain bounded read (see
    X402PromoStmts.LIST_ALL_PROMO_CODES) rather than anything the redemption
    hot path touches. `remaining` is null for a row when Redis could not be
    reached for that code (see promo.list_promo_codes) -- a Redis blip
    degrades one field, not the whole listing.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    return {"items": list_promo_codes()}


def x402_admin_create_promo(request: Request) -> Response | dict:
    """Admin: issue a promo code scoped to one resource for a bounded number of free redemptions.

    Same shape as the directory's admin delist: session wallet verified
    first -- the X-Admin-Wallet header is never trusted. See
    app/modules/x402/promo.py for what the code lets a caller skip and the
    abuse guards around it.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    try:
        payload = serialization.decode(request.body, X402PromoCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        record = create_promo_code(
            code=payload.code,
            resource=payload.resource,
            starting_count=payload.starting_count,
            expires_at_epoch=payload.expires_at_epoch,
            max_redemptions_per_wallet=payload.max_redemptions_per_wallet,
        )
    except PromoError as exc:
        return json_error_from_platform(exc)

    return {
        "code": record.code,
        "resource": record.resource,
        "starting_count": record.starting_count,
        "created_at_epoch": record.created_at_epoch,
        "expires_at_epoch": record.expires_at_epoch,
        "active": record.active,
        "max_redemptions_per_wallet": record.max_redemptions_per_wallet,
    }


def x402_admin_delete_promo(request: Request) -> Response | dict:
    """Admin: deactivate one promo code early, without waiting for its uses to run out.

    Takes the code as a query param, not a body: DELETE requests carry no
    body convention elsewhere in this backend (same reasoning as the
    directory's admin delist).
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    raw_code = query_param(request.query_params.get("code", ""))
    if not raw_code:
        return json_error_response(400, "invalid_request", "code is required")

    deactivated = deactivate_promo_code(raw_code)
    if not deactivated:
        return json_error_response(404, "not_found", "No promo code with that code")
    return {"deactivated": True, "code": raw_code}


def x402_admin_reset_refund_breaker(request: Request) -> Response | dict:
    """Admin: clear a resource's tripped refund circuit breaker (modules/x402/circuit_breaker.py).

    Takes the resource id as a query param, not a body -- same convention as
    the promo/delist admin actions. Not idempotent in the sense that matters
    to an operator: resetting a breaker that was not tripped is a harmless
    no-op (the Redis key simply may not have existed), so this always
    reports success rather than distinguishing "was tripped" from "already
    clear" -- there is nothing actionable in that distinction for the admin.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    resource = query_param(request.query_params.get("resource", ""))
    if not resource:
        return json_error_response(400, "invalid_request", "resource is required")

    circuit_breaker.reset(resource)
    return {"reset": True, "resource": resource}


def register_x402_catalog_routes(app: Router) -> None:
    """Register the free catalog route, the free recent-settlements proof-of-volume feed, the paid ping, and the admin promo-code routes."""
    app.get(CATALOG_PATH)(x402_catalog)
    app.get("/api/v1/x402/settlements/recent")(x402_recent_settlements)
    # x402-marketplace-ux-audit.md section 3.3 "meta": /settlements is the
    # clarified name ("/recent" was the only mode there ever was); the old
    # path stays registered to the identical handler, never removed.
    app.get("/api/v1/x402/settlements")(x402_recent_settlements)
    app.get("/api/v1/x402/ping")(x402_ping)
    app.get("/api/v1/admin/x402/promo")(x402_admin_list_promo)
    app.post("/api/v1/admin/x402/promo")(x402_admin_create_promo)
    app.delete("/api/v1/admin/x402/promo")(x402_admin_delete_promo)
    app.post("/api/v1/admin/x402/refund-breaker/reset")(x402_admin_reset_refund_breaker)
