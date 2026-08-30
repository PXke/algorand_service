"""HTTP route for the x402 catalog: free, rate-limited per IP.

Served at /api/v1/x402 only. A /.well-known/x402 alias is NOT registered:
nginx proxies only `location ^~ /api/` and `/health/ready` to this backend on
the API host and answers everything else with 404
(deploy/nginx/algorand-platform.conf), so a top-level path would be
unreachable in production.
"""

from __future__ import annotations

from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.x402_catalog.services.catalog import CATALOG_PATH, build_catalog
from app.modules.x402_catalog.services.rate_limit import catalog_rate_limited


def x402_catalog(request: Request) -> Response | dict:
    """Free: the machine-readable index of every live x402 route, rate-limited per IP."""
    if catalog_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many catalog requests — please try again later"
        )
    return build_catalog()


def register_x402_catalog_routes(app: Router) -> None:
    """Register the free catalog route."""
    app.get(CATALOG_PATH)(x402_catalog)
