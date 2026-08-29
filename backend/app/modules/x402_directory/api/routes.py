"""HTTP routes for the x402 endpoint directory: paid listing, free search.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/list would be unreachable in production without an nginx change
this change is not authorized to deploy.
"""

from __future__ import annotations

from x402.extensions.bazaar import declare_discovery_extension
from x402.extensions.bazaar.resource_service import OutputConfig

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.x402.paid_request import require_paid_request
from app.modules.x402_directory.models.domain import DirectoryError, StoredListing
from app.modules.x402_directory.services.listing_service import ListingService
from app.modules.x402_directory.services.rate_limit import search_rate_limited
from app.schemas import X402ListingRequest

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by both routes.
listing_service = ListingService()

_LISTING_EXAMPLE = {
    "url": "https://api.example.com/v1/quote",
    "price": "$0.01",
    "description": "Live FX quote, one currency pair per call.",
    "assets": ["USDC"],
    "tags": ["fx", "market-data"],
}


def _listing_json(item: StoredListing) -> dict:
    """Serialize a stored listing for the wire, re-inflating schema_json to an object."""
    return {
        "url": item.url,
        "price": item.price,
        "description": item.description,
        "assets": item.assets,
        "tags": item.tags,
        "schema": serialization.decode(item.schema_json, dict) if item.schema_json else None,
        "term_end_epoch": item.term_end_epoch,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
    }


def x402_list(request: Request) -> Response:
    """Paid: list an x402 endpoint in the directory for a fixed term.

    The body is parsed and validated BEFORE the payment gate runs, so a
    malformed request is rejected with a 400 without anyone being charged for
    it. Nothing is written before the payment settles, and once it has settled
    the listing is stored and returned — a settled payment never yields a 4xx.
    """
    try:
        payload = serialization.decode(request.body, X402ListingRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    term_days = settings.x402_listing_term_days
    result = require_paid_request(
        request,
        price=settings.x402_listing_price,
        resource="x402-directory-list",
        # Reaches the payer as the 402's resource.description, before they
        # commit — the term length is not derivable from the price alone.
        description=(
            f"List one x402 endpoint in the public PXke x402 directory for {term_days} days. "
            f"Discoverable immediately at GET /api/v1/x402/search."
        ),
        extensions=declare_discovery_extension(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one (declare_discovery_extension's body_type=None
            # branch), which would describe this route's input incorrectly.
            body_type="json",
            input=_LISTING_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": 2048},
                    "price": {"type": "string", "maxLength": 64},
                    "description": {"type": "string", "maxLength": 2000},
                    "assets": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "schema": {"type": "object"},
                },
                "required": ["url", "price"],
            },
            # OutputConfig, not a bare dict: the package reads `output.example`
            # as an attribute and an AttributeError here would 500 the route.
            output=OutputConfig(
                example={
                    "listing": {**_LISTING_EXAMPLE, "term_end_epoch": 0},
                    "settlement_tx_id": "...",
                    "term_days": term_days,
                }
            ),
        ),
    )
    if result.error:
        return result.error

    try:
        listing = listing_service.create(
            url=payload.url,
            price=payload.price,
            description=payload.description,
            assets=payload.assets,
            tags=payload.tags,
            schema=payload.schema,
            settlement_tx_id=result.payment_txid or "",
        )
    except DirectoryError as exc:
        # Unreachable in practice — the URL is validated during decode and the
        # only other raiser is normalize_url, which runs on the same input.
        # Kept explicit so a future validation rule cannot silently 500 a
        # request whose payment has already been taken.
        return json_error_from_platform(exc)

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "listing": _listing_json(listing),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
            }
        ),
    )


def x402_search(request: Request) -> Response | dict:
    """Free: the directory's listings, newest first, rate-limited per IP."""
    if search_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many search requests — please try again later"
        )

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_search_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = listing_service.search(limit=limit)
    return {"items": [_listing_json(item) for item in items]}


def register_x402_directory_routes(app: Router) -> None:
    """Register the paid directory-listing route and the free search route."""
    app.post("/api/v1/x402/list")(x402_list)
    app.get("/api/v1/x402/search")(x402_search)
