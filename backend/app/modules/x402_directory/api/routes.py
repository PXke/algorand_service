"""HTTP routes for the x402 endpoint directory: paid listing and renewal, free search, detail and probe status, admin delist.

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
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request
from app.modules.x402_directory.models.domain import (
    CATEGORY_TAG_PREFIX,
    LISTING_CATEGORIES,
    DirectoryError,
    StoredListing,
    StoredProbe,
)
from app.modules.x402_directory.services.listing_service import (
    MAX_SCHEMA_JSON_BYTES,
    MAX_TAG_LENGTH,
    ListingService,
    encode_schema,
    normalize_url,
    validate_category,
    validate_tags,
)
from app.modules.x402_directory.services.rate_limit import search_rate_limited
from app.schemas import X402ListingRenewRequest, X402ListingRequest

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by every route.
listing_service = ListingService()

_LISTING_EXAMPLE = {
    "url": "https://api.example.com/v1/quote",
    "price": "$0.01",
    "description": "Live FX quote, one currency pair per call.",
    "assets": ["USDC"],
    "tags": ["fx", "market-data"],
    "category": "finance",
}

# What a listing looks like on the wire, for the discovery output examples
# of every route that returns one.
_LISTING_OUTPUT_EXAMPLE = {
    **_LISTING_EXAMPLE,
    "schema": None,
    "term_end_epoch": 0,
    "created_at_epoch": 0,
    "settlement_tx_id": "...",
    "payer": "...",
    "verified_wallet": "",
    "verified_at_epoch": 0,
}


def _listing_json(item: StoredListing) -> dict:
    """Serialize a stored listing for the wire, re-inflating schema_json to an object."""
    return {
        "url": item.url,
        "price": item.price,
        "description": item.description,
        "assets": item.assets,
        "tags": item.tags,
        "category": item.category,
        "schema": serialization.decode(item.schema_json, dict) if item.schema_json else None,
        "term_end_epoch": item.term_end_epoch,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
        "payer": item.payer,
        # Badge (migration 097): served only while it still belongs to the
        # current payer, see StoredListing.is_verified.
        "verified_wallet": item.verified_wallet if item.is_verified else "",
        "verified_at_epoch": item.verified_at_epoch if item.is_verified else 0,
    }


def _probe_json(probe: StoredProbe) -> dict:
    """Serialize the newest probe of one listing for the wire."""
    return {
        "probed_at_epoch": probe.probed_at_epoch,
        "reachable": probe.reachable,
        "http_status": probe.http_status,
        "latency_ms": probe.latency_ms,
        "served_valid_402": probe.served_valid_402,
        "payto_seen": probe.payto_seen,
        "error": probe.error,
    }


def x402_list(request: Request) -> Response:
    """Paid: list an x402 endpoint in the directory for a fixed term.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and its fields range-checked, the URL is normalized
    (scheme/host validity), the request schema is encoded and size-checked,
    the category is checked against the fixed enum, and the tags are checked
    for the reserved `category:` namespace. All are 400s nobody pays for. The
    gate runs only once the request is known to be storable.

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
        category = validate_category(payload.category)
        tags = validate_tags(payload.tags)
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
            f"Discoverable immediately at GET /api/v1/x402/search (free; optional "
            f"`?tag=<tag>` filters to listings carrying that tag, `?category=<category>` "
            f"to listings in that category, `?limit=` caps results) and at "
            f"GET /api/v1/x402/listings?url=<url> (free; the full listing plus its latest "
            f"probe), and removed from both when the {term_days} days are up "
            f"(extend with POST /api/v1/x402/list/renew). `tags` are stored trimmed and "
            f"lowercased and are what `?tag=` matches on; tags starting with "
            f"`{CATEGORY_TAG_PREFIX}` are reserved. Optional `category` is one of "
            f"{', '.join(LISTING_CATEGORIES)} (default other). Optional `schema` must "
            f"serialize to at most {MAX_SCHEMA_JSON_BYTES} bytes."
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
                        "items": {"type": "string", "maxLength": MAX_TAG_LENGTH},
                        "description": (
                            "Stored trimmed and lowercased; searchable via "
                            f"GET /api/v1/x402/search?tag=<tag>. Tags starting with "
                            f"`{CATEGORY_TAG_PREFIX}` are reserved and rejected."
                        ),
                    },
                    "category": {
                        "type": "string",
                        "enum": list(LISTING_CATEGORIES),
                        "default": "other",
                        "description": (
                            "Fixed facet; searchable via "
                            "GET /api/v1/x402/search?category=<category>."
                        ),
                    },
                    # JSON Schema has no serialized-byte-size keyword, so the
                    # 4 KiB cap enforced by encode_schema() above cannot be
                    # declared here; it is stated in the description instead.
                    "schema": {"type": "object"},
                },
                "required": ["url", "price"],
            },
            output_example={
                "listing": _LISTING_OUTPUT_EXAMPLE,
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
            tags=tags,
            schema_json=schema_json,
            settlement_tx_id=result.payment_txid or "",
            payer=result.payer or "",
            category=category,
        )
    except DirectoryError as exc:
        # Reachable now (migration 094): a relist attempt by a different
        # payer than the current owner is refused here, payment already
        # taken — see listing_service.create()'s ownership check. That check
        # is the ONLY reachable raiser on this side of the gate: the URL,
        # the schema, the category and the tags were all validated above,
        # before it. A new validation rule belongs there too, never here.
        return json_error_from_platform(exc)

    mark_fulfilled(result.payment_txid, resource="x402-directory-list")
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


def x402_renew(request: Request) -> Response:
    """Paid: extend an existing listing's term by one more term, at the listing price.

    Decoded, normalized and looked up BEFORE the payment gate: a malformed
    body, an invalid url and a url that is not listed are all free 400s/404s.
    Ownership cannot be checked before the gate -- the payer is only known
    once the payment has settled -- so a renewal by a wallet other than the
    one that listed the url is refused with the payment already taken and
    the listing untouched (403 listing_owned_by_another_payer while the term
    runs, 409 renew_requires_relist once it has expired; receipt headers
    served either way). Same accepted tradeoff as x402_list's relist check
    and the board's renew, and the 402 offer says so before the payer
    commits. See listing_service.renew() for the term arithmetic and what a
    renewal does and does not change.
    """
    try:
        payload = serialization.decode(request.body, X402ListingRenewRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        normalized_url = normalize_url(payload.url)
    except DirectoryError as exc:
        return json_error_from_platform(exc)
    if listing_service.probe_status(normalized_url) is None:
        return json_error_response(404, "not_found", "No listing for that url")

    term_days = settings.x402_listing_term_days
    result = require_paid_request(
        request,
        price=settings.x402_listing_price,
        resource="x402-directory-renew",
        description=(
            f"Extend an existing PXke x402 directory listing by {term_days} more days, "
            f"from the later of now and its current term end; nothing else about the "
            f"listing changes. Only the wallet that listed the url may renew it, "
            f"before or after its term ends: a payment from any other wallet settles "
            f"but is refused and changes nothing (403 while the term is running, 409 "
            f"renew_requires_relist once it has expired). To take over an expired "
            f"or unowned url, POST /api/v1/x402/list to relist it under your wallet."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input={"url": _LISTING_EXAMPLE["url"]},
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "maxLength": 2048}},
                "required": ["url"],
            },
            output_example={
                "listing": _LISTING_OUTPUT_EXAMPLE,
                "settlement_tx_id": "...",
                "term_days": term_days,
            },
        ),
    )
    if result.error:
        return result.error

    try:
        renewed = listing_service.renew(
            normalized_url=normalized_url,
            payer=result.payer or "",
            settlement_tx_id=result.payment_txid or "",
        )
    except DirectoryError as exc:
        # The ownership refusal (or, only if the listing was admin-deleted
        # between the pre-gate lookup and now, not_found): payment taken,
        # nothing changed. The settlement headers are still served so the
        # payer has their receipt.
        return Response(
            status_code=exc.http_status,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps({"error": {"code": exc.code, "message": exc.message}}),
        )

    mark_fulfilled(result.payment_txid, resource="x402-directory-renew")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "listing": _listing_json(renewed),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
            }
        ),
    )


def x402_search(request: Request) -> Response | dict:
    """Free: the directory's unexpired listings, newest first, rate-limited per IP.

    Query params: `limit` (integer, clamped to settings.x402_search_max_results),
    optional `tag` (1-MAX_TAG_LENGTH characters, matched trimmed and
    lowercased against the tags a listing was stored with) and optional
    `category` (one of LISTING_CATEGORIES); `tag` and `category` together are
    a 400. Listings whose paid term has ended are excluded — see
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

    raw_tag = query_param(request.query_params.get("tag", ""))
    raw_category = query_param(request.query_params.get("category", ""))
    try:
        items = listing_service.search(
            limit=limit, tag=raw_tag or None, category=raw_category or None
        )
    except DirectoryError as exc:
        return json_error_from_platform(exc)
    return {"items": [_listing_json(item) for item in items]}


def x402_listing_detail(request: Request) -> Response | dict:
    """Free: everything the directory holds about one listed URL, rate-limited per IP.

    The listing in the same shape search serves it, plus `probe` (the newest
    unpaid probe result, null before the first probe). Shares the search
    route's per-IP hourly budget: same free read path over the same
    directory. A url that is not listed, or whose paid term has ended, is a
    404 -- this is the per-url twin of search, which has stopped serving it.
    """
    if search_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many listing requests — please try again later"
        )
    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")
    try:
        normalized_url = normalize_url(raw_url)
    except DirectoryError as exc:
        return json_error_from_platform(exc)

    found = listing_service.detail(normalized_url)
    if found is None:
        return json_error_response(404, "not_found", "No live listing for that url")
    listing, probe = found
    return {
        "listing": _listing_json(listing),
        "probe": _probe_json(probe) if probe is not None else None,
    }


def x402_probe_status(request: Request) -> Response | dict:
    """Free: the newest unpaid probe result for one listed URL, rate-limited per IP.

    Shares the search route's per-IP hourly budget (search_rate_limited):
    it is the same free read path over the same directory. `probe` is null
    for a listing the beat has not reached yet; an unlisted URL is a 404.
    """
    if search_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many probe requests — please try again later"
        )
    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")
    try:
        normalized_url = normalize_url(raw_url)
    except DirectoryError as exc:
        return json_error_from_platform(exc)

    found = listing_service.probe_status(normalized_url)
    if found is None:
        return json_error_response(404, "not_found", "No listing for that url")
    listing, probe = found
    return {
        "url": listing.url,
        "verified_wallet": listing.verified_wallet if listing.is_verified else "",
        "verified_at_epoch": listing.verified_at_epoch if listing.is_verified else 0,
        "probe": _probe_json(probe) if probe is not None else None,
    }


def x402_admin_delete_listing(request: Request) -> Response | dict:
    """Admin: delist a url outright, without waiting out its paid term.

    For an ownership dispute or an abuse report -- the only other way a
    listing currently leaves the directory is its term expiring (see
    listing_service.search()). Takes the url as a query param, not a path
    segment: a full url contains slashes and query characters that a REST
    path segment cannot carry cleanly, and not a body: DELETE requests carry
    no body convention elsewhere in this backend.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")
    try:
        normalized_url = normalize_url(raw_url)
    except DirectoryError as exc:
        return json_error_from_platform(exc)

    deleted = listing_service.delete(normalized_url)
    if not deleted:
        return json_error_response(404, "not_found", "No listing for that url")
    return {"deleted": True, "url": normalized_url}


def register_x402_directory_routes(app: Router) -> None:
    """Register the paid list and renew routes, the free search, detail and probe-status routes, and the admin delist route."""
    app.post("/api/v1/x402/list")(x402_list)
    app.post("/api/v1/x402/list/renew")(x402_renew)
    app.get("/api/v1/x402/search")(x402_search)
    app.get("/api/v1/x402/listings")(x402_listing_detail)
    app.get("/api/v1/x402/directory/probe")(x402_probe_status)
    app.delete("/api/v1/admin/x402/listings")(x402_admin_delete_listing)
