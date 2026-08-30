"""HTTP routes for the x402 endpoint directory: paid listing, free search.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/list would be unreachable in production without an nginx change
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
from app.modules.x402_directory.models.domain import DirectoryError, StoredListing
from app.modules.x402_directory.services.listing_service import (
    MAX_SCHEMA_JSON_BYTES,
    ListingService,
    encode_schema,
    normalize_url,
)
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

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and its fields range-checked, the URL is normalized
    (scheme/host validity), and the request schema is encoded and size-checked.
    All three are 400s nobody pays for. The gate runs only once the request is
    known to be storable.

    After the gate, the one remaining way to fail is the ownership check in
    listing_service.create() — a relist attempt against a URL another payer
    owns, which cannot be evaluated before the gate because the payer's
    identity does not exist until the payment settles. That is the only
    settled-payment-yields-a-4xx path on this route, and it is deliberate; see
    create()'s docstring.
    """
    try:
        payload = serialization.decode(request.body, X402ListingRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        normalized_url = normalize_url(payload.url)
        schema_json = encode_schema(payload.schema)
    except DirectoryError as exc:
        return json_error_from_platform(exc)

    term_days = settings.x402_listing_term_days
    result = require_paid_request(
        request,
        price=settings.x402_listing_price,
        resource="x402-directory-list",
        # Reaches the payer as the 402's resource.description, before they
        # commit — the term length is not derivable from the price alone.
        description=(
            f"List one x402 endpoint in the public PXke x402 directory for {term_days} days. "
            f"Discoverable immediately at GET /api/v1/x402/search, and removed from it when "
            f"the {term_days} days are up. Optional `schema` must serialize to at most "
            f"{MAX_SCHEMA_JSON_BYTES} bytes."
        ),
        extensions=describe_json_endpoint(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one, which would describe this route's input
            # incorrectly.
            body_type="json",
            input=_LISTING_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": 2048},
                    "price": {"type": "string", "maxLength": 64},
                    "description": {"type": "string", "maxLength": 2000},
                    "assets": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {"type": "string", "maxLength": 64},
                    },
                    "tags": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {"type": "string", "maxLength": 64},
                    },
                    # JSON Schema has no serialized-byte-size keyword, so the
                    # 4 KiB cap enforced by encode_schema() above cannot be
                    # declared here; it is stated in the description instead.
                    "schema": {"type": "object"},
                },
                "required": ["url", "price"],
            },
            output_example={
                "listing": {**_LISTING_EXAMPLE, "term_end_epoch": 0},
                "settlement_tx_id": "...",
                "term_days": term_days,
            },
        ),
    )
    if result.error:
        return result.error

    try:
        listing = listing_service.create(
            normalized_url=normalized_url,
            price=payload.price,
            description=payload.description,
            assets=payload.assets,
            tags=payload.tags,
            schema_json=schema_json,
            settlement_tx_id=result.payment_txid or "",
            payer=result.payer or "",
        )
    except DirectoryError as exc:
        # Reachable now (migration 094): a relist attempt by a different
        # payer than the current owner is refused here, payment already
        # taken — see listing_service.create()'s ownership check. That check
        # is the ONLY reachable raiser on this side of the gate: the URL and
        # the schema were both validated above, before it. A new validation
        # rule belongs there too, never here.
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
    """Free: the directory's unexpired listings, newest first, rate-limited per IP.

    Listings whose paid term has ended are excluded — see
    listing_service.search().
    """
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
