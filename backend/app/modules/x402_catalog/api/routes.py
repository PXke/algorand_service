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

from app.core import serialization
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402 import circuit_breaker
from app.modules.x402.promo import (
    PromoError,
    create_promo_code,
    deactivate_promo_code,
    list_promo_codes,
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

    Session wallet verified first -- the X-Admin-Wallet header is never
    trusted. See
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
    body convention elsewhere in this backend.
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
    the promo admin actions. Not idempotent in the sense that matters
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
    """Register the free catalog route, the free recent-settlements proof-of-volume feed, and the admin promo-code / refund-breaker routes."""
    app.get(CATALOG_PATH)(x402_catalog)
    app.get("/api/v1/x402/settlements/recent")(x402_recent_settlements)
    # x402-marketplace-ux-audit.md section 3.3 "meta": /settlements is the
    # clarified name ("/recent" was the only mode there ever was); the old
    # path stays registered to the identical handler, never removed.
    app.get("/api/v1/x402/settlements")(x402_recent_settlements)
    app.get("/api/v1/admin/x402/promo")(x402_admin_list_promo)
    app.post("/api/v1/admin/x402/promo")(x402_admin_create_promo)
    app.delete("/api/v1/admin/x402/promo")(x402_admin_delete_promo)
    app.post("/api/v1/admin/x402/refund-breaker/reset")(x402_admin_reset_refund_breaker)
