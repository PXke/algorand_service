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
from app.core.http_errors import json_error_response
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
from app.modules.x402_catalog.services.catalog import (
    CATALOG_PATH,
    build_catalog,
    recent_settlements_json,
)
from app.modules.x402_catalog.services.rate_limit import (
    catalog_rate_limited,
    settlements_rate_limited,
)


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
    """
    result = require_paid_request(
        request,
        price=settings.x402_ping_price,
        resource="x402-ping",
        description=(
            "Integration test: proves your x402 client can build, sign and settle a real "
            "payment against this marketplace's facilitator setup at the lowest price we "
            "offer, before you risk money on an actual product."
        ),
        extensions=describe_json_endpoint(
            input=None,
            output_example={"pong": True, "settlement_tx_id": "...", "served_at_epoch": 0},
        ),
    )
    if result.error:
        return result.error

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


def register_x402_catalog_routes(app: Router) -> None:
    """Register the free catalog route, the free recent-settlements proof-of-volume feed, and the paid ping."""
    app.get(CATALOG_PATH)(x402_catalog)
    app.get("/api/v1/x402/settlements/recent")(x402_recent_settlements)
    app.get("/api/v1/x402/ping")(x402_ping)
