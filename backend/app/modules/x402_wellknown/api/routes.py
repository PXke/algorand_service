"""HTTP routes for the marketplace's discovery bootstrap surface.

`GET /.well-known/x402` -- the emergent (non-ratified) discovery-manifest
convention several x402 directories (gold-402, x402 List, and others) already
crawl for; no single spec fixes its shape (searched 2026-08-31: neither
coinbase/x402's own specification nor the IETF x402 drafts define a
`/.well-known/` convention -- it is a convention some directories adopted on
their own, not a standard). Rather than invent fields against an unconfirmed
spec, this route re-serves the EXACT SAME document `/api/v1/x402` already
produces (`build_catalog()`) -- already a complete, well-formed description
of the marketplace and its live routes/prices/assets.

`GET /openapi.json` -- a real OpenAPI 3.1 document, generated in
services/openapi_spec.py from the same catalog roster rather than a second
hand-written route list.

Both are free and share the catalog's own per-IP hourly rate-limit budget
(`x402_catalog_rate_limit_per_hour` via `x402_catalog`'s `catalog_rate_limited`):
same cost profile as `GET /api/v1/x402` (a pure settings read, no
Cassandra/Typesense query) and, if anything, heavier expected crawl traffic
-- these are the paths peer directories are expected to hit FIRST. Reusing
the existing counter and setting avoids a new one for what is, in substance,
the same document under a different path (CLAUDE.md section 3: config has
one owner).

Reachable at the top level (not under `/api/`) requires two dedicated nginx
`location` blocks on the API host -- see
deploy/nginx/algorand-platform.conf's API server block.
"""

from __future__ import annotations

from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.x402_catalog.services.catalog import build_catalog
from app.modules.x402_catalog.services.rate_limit import catalog_rate_limited
from app.modules.x402_wellknown.services.openapi_spec import (
    OPENAPI_PATH,
    WELLKNOWN_PATH,
    build_openapi,
)


def x402_wellknown(request: Request) -> Response | dict:
    """Free: the discovery manifest at the conventional well-known URI, verbatim from build_catalog()."""
    if catalog_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many discovery-manifest requests — please try again later"
        )
    return build_catalog()


def x402_openapi(request: Request) -> Response | dict:
    """Free: an OpenAPI 3.1 document for every currently-live x402 route."""
    if catalog_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many OpenAPI-document requests — please try again later"
        )
    return build_openapi()


def register_x402_wellknown_routes(app: Router) -> None:
    """Register the well-known discovery manifest and the OpenAPI document."""
    app.get(WELLKNOWN_PATH)(x402_wellknown)
    app.get(OPENAPI_PATH)(x402_openapi)
