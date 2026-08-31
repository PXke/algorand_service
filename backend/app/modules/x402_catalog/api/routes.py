"""HTTP route for the x402 catalog: free, rate-limited per IP.

Served at /api/v1/x402 only. A /.well-known/x402 alias is NOT registered:
nginx proxies only `location ^~ /api/` and `/health/ready` to this backend on
the API host and answers everything else with 404
(deploy/nginx/algorand-platform.conf), so a top-level path would be
unreachable in production.
"""

from __future__ import annotations

import time

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
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
    """
    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_ping_price,
        resource="x402-ping",
        description=(
            "Integration test: proves your x402 client can build, sign and settle a real "
            "payment against this marketplace's facilitator setup at the lowest price we "
            "offer, before you risk money on an actual product. Supports ?preview=true "
            "(redacted, unpaid, rate-limited) and an admin-issued ?promo=CODE&"
            "promo_wallet=ADDRESS bypass (real response, still no payment)."
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

    mark_fulfilled(result.payment_txid, resource="x402-ping")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "pong": True,
                "settlement_tx_id": result.payment_txid or "",
                "served_at_epoch": int(time.time()),
            }
        ),
    )


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


def register_x402_catalog_routes(app: Router) -> None:
    """Register the free catalog route, the free recent-settlements proof-of-volume feed, the paid ping, and the admin promo-code routes."""
    app.get(CATALOG_PATH)(x402_catalog)
    app.get("/api/v1/x402/settlements/recent")(x402_recent_settlements)
    app.get("/api/v1/x402/ping")(x402_ping)
    app.get("/api/v1/admin/x402/promo")(x402_admin_list_promo)
    app.post("/api/v1/admin/x402/promo")(x402_admin_create_promo)
    app.delete("/api/v1/admin/x402/promo")(x402_admin_delete_promo)
