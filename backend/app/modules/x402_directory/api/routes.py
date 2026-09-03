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
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402.promo import promo_request_params
from app.modules.x402_directory.models.domain import (
    CATEGORY_TAG_PREFIX,
    LISTING_CATEGORIES,
    DirectoryError,
    StoredListing,
    StoredProbe,
)
from app.modules.x402_directory.services.listing_service import (
    MAX_CONTACT_LENGTH,
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

_LIST_RESOURCE = "x402-directory-list"
_RENEW_RESOURCE = "x402-directory-renew"

_LISTING_EXAMPLE = {
    "url": "https://api.example.com/v1/quote",
    "price": "$0.01",
    "description": "Live FX quote, one currency pair per call.",
    "assets": ["USDC"],
    "tags": ["fx", "market-data"],
    "category": "finance",
    "reimburses": False,
    "contact": "support@example.com",
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
        # Self-declared (104), never verified by us for a third-party
        # listing -- see StoredListing's own field comments.
        "reimburses": item.reimburses,
        "contact": item.contact,
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
    identity does not exist until the payment settles. This raises
    DirectoryError, a PlatformError subclass, so run_with_refund's own
    contract (2026-09-02 retrofit; see its docstring) treats it as
    "payment settles but is refused" and returns the 403/409 directly --
    NEVER a refund, and never counted against the circuit breaker. That is
    deliberate: the payer fully controls whether they trigger this (paying
    to relist a URL they know they don't own), so refunding it would make
    it a free, repeatable way to pump the breaker -- exactly the
    found-in-audit gap (2026-09-02) this route briefly had before
    run_with_refund grew its PlatformError exemption. The promo branch
    below settles nothing either way, so it keeps its own direct-4xx path
    regardless.
    """
    if circuit_breaker.is_tripped(_LIST_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

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
    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_listing_price,
        resource=_LIST_RESOURCE,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
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
            f"serialize to at most {MAX_SCHEMA_JSON_BYTES} bytes. Optional `reimburses` "
            f"(default false) is your own SELF-DECLARED, UNVERIFIED claim that you refund "
            f"a payer when your endpoint fails to deliver -- we do not check this for a "
            f"third-party listing, it is exactly as trustworthy as you are. Optional "
            f"`contact` (at most {MAX_CONTACT_LENGTH} characters) is a point of contact "
            f"for issues. Both are set only when you list or relist -- POST "
            f"/api/v1/x402/list/renew changes nothing about the listing but its term."
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
                    "reimburses": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Self-declared, unverified: do you refund a payer when your "
                            "endpoint fails to deliver? Not checked by us for a "
                            "third-party listing."
                        ),
                    },
                    "contact": {"type": "string", "maxLength": MAX_CONTACT_LENGTH},
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

    if result.is_promo:
        # Nothing settled, so there is nothing for run_with_refund to
        # refund -- keep the original direct-4xx-no-refund behavior for the
        # one reachable failure (an ownership conflict on the relisted url).
        try:
            outcome = _list_product_write(
                normalized_url=normalized_url,
                payload=payload,
                tags=tags,
                schema_json=schema_json,
                result=result,
                promo_wallet=promo_wallet,
                category=category,
            )
        except DirectoryError as exc:
            return json_error_from_platform(exc)
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps(
                {**outcome, "settlement_tx_id": "", "term_days": term_days, "via": "promo"}
            ),
        )

    outcome = run_with_refund(
        result,
        resource=_LIST_RESOURCE,
        product_write=lambda: _list_product_write(
            normalized_url=normalized_url,
            payload=payload,
            tags=tags,
            schema_json=schema_json,
            result=result,
            promo_wallet=promo_wallet,
            category=category,
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_LIST_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {**outcome, "settlement_tx_id": result.payment_txid or "", "term_days": term_days}
        ),
    )


def _list_product_write(
    *,
    normalized_url: str,
    payload: X402ListingRequest,
    tags: list[str],
    schema_json: str,
    result: PaymentResult,
    promo_wallet: str,
    category: str,
) -> dict:
    """The product write x402_list protects (via run_with_refund for a real payment, direct for promo): create the listing.

    Returns a plain dict, never a Response -- see _ping_product_write's own
    docstring in x402_catalog/api/routes.py for why a successful product
    write must never itself return a Response (the isinstance(outcome,
    Response) check both callers above rely on would become ambiguous).
    Raises DirectoryError on the one reachable failure: a relist attempt by
    a wallet that does not own the url (listing_service.create()'s ownership
    check) -- the URL, schema, category and tags were already validated
    before the gate, so nothing else here can raise.
    """
    listing = listing_service.create(
        normalized_url=normalized_url,
        price=payload.price,
        description=payload.description,
        assets=payload.assets,
        tags=tags,
        schema_json=schema_json,
        settlement_tx_id=result.payment_txid or "",
        # A promo redemption settles nothing, so result.payer is empty —
        # attribute the listing to the caller's own claimed wallet instead
        # (already validated in promo.attempt_promo_redemption).
        payer=result.payer or promo_wallet,
        category=category,
        reimburses=payload.reimburses,
        contact=payload.contact,
    )
    return {"listing": _listing_json(listing)}


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

    For a REAL payment (not promo), the ownership refusal still goes
    through run_with_refund (2026-09-02 retrofit) like x402_list's
    identical case -- but DirectoryError is a PlatformError, so
    run_with_refund's own contract treats this exactly as documented above:
    payment kept, receipt served, NEVER a refund and never counted against
    the circuit breaker (see run_with_refund's own docstring, and
    x402_list's docstring for why refunding a fully caller-controlled
    rejection would have been a free way to pump the breaker). A promo
    redemption never settles a real payment, so it keeps its own
    direct-4xx path regardless -- see the promo branch below.
    """
    if circuit_breaker.is_tripped(_RENEW_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

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
    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_listing_price,
        resource=_RENEW_RESOURCE,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
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

    if result.is_promo:
        # Nothing settled, so there is nothing for run_with_refund to
        # refund -- keep the original direct-4xx-no-refund behavior, with
        # the settlement headers still attached so the payer has a receipt.
        try:
            renewed = _renew_product_write(
                normalized_url=normalized_url, result=result, promo_wallet=promo_wallet
            )
        except DirectoryError as exc:
            return Response(
                status_code=exc.http_status,
                headers={"Content-Type": "application/json", **result.settlement_headers},
                description=serialization.dumps(
                    {"error": {"code": exc.code, "message": exc.message}}
                ),
            )
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps(
                {
                    "listing": _listing_json(renewed),
                    "settlement_tx_id": "",
                    "term_days": term_days,
                    "via": "promo",
                }
            ),
        )

    outcome = run_with_refund(
        result,
        resource=_RENEW_RESOURCE,
        product_write=lambda: _renew_product_write(
            normalized_url=normalized_url, result=result, promo_wallet=promo_wallet
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RENEW_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "listing": _listing_json(outcome),
                "settlement_tx_id": result.payment_txid or "",
                "term_days": term_days,
            }
        ),
    )


def _renew_product_write(
    *, normalized_url: str, result: PaymentResult, promo_wallet: str
) -> StoredListing:
    """The product write x402_renew protects (via run_with_refund for a real payment, direct for promo): extend the listing's term.

    Returns the renewed StoredListing directly, never a Response -- unlike
    _list_product_write, the caller wraps this return value into the final
    JSON itself (the renew response shape is simpler, just the listing), so
    there is no dict/Response ambiguity to guard against here: a StoredListing
    is never mistaken for a Response by the isinstance check both callers
    above rely on. Raises DirectoryError on the one reachable failure: a
    renewal attempt by a wallet that does not own the url
    (listing_service.renew()'s ownership check) -- the url's existence was
    already confirmed before the gate, so nothing else here can raise.
    """
    return listing_service.renew(
        normalized_url=normalized_url,
        # See x402_list's identical fallback: a promo redemption settles
        # nothing, so result.payer is empty on a promo result.
        payer=result.payer or promo_wallet,
        settlement_tx_id=result.payment_txid or "",
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


def x402_probe_history(request: Request) -> Response | dict:
    """Free: up to x402_probe_history_max_results past probe results for one listed URL, newest first, rate-limited per IP.

    Shares the search route's per-IP hourly budget (search_rate_limited),
    same free read path over the same directory as x402_probe_status. Real
    measured uptime/latency history (roadmap item 7) rather than the single
    latest reading -- kept free rather than priced (2026-08-31 owner call):
    the marketplace benefits more from this working as a trust signal an
    agent (or a third party) can point to freely than as its own paid
    product. `limit` is clamped server-side; an unlisted URL is a 404, same
    as x402_probe_status. Empty `history` is a listed URL the beat has not
    reached yet, not an error.
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

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_probe_history_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    history = listing_service.probe_history(normalized_url, limit=limit)
    if history is None:
        return json_error_response(404, "not_found", "No listing for that url")
    return {"url": normalized_url, "history": [_probe_json(probe) for probe in history]}


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
    """Register the paid list and renew routes, the free search/detail/probe-status/probe-history routes, and the admin delist route."""
    app.post("/api/v1/x402/list")(x402_list)
    app.post("/api/v1/x402/list/renew")(x402_renew)
    app.get("/api/v1/x402/search")(x402_search)
    app.get("/api/v1/x402/listings")(x402_listing_detail)
    app.get("/api/v1/x402/directory/probe")(x402_probe_status)
    app.get("/api/v1/x402/directory/probe/history")(x402_probe_history)
    app.delete("/api/v1/admin/x402/listings")(x402_admin_delete_listing)
