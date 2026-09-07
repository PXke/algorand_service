"""HTTP routes for the x402 endpoint directory: paid listing and boost, free search, detail and probe status, admin delist.

A listing now survives for as long as it keeps passing health probes
(workers/app/modules/x402_probe extends term_end on every healthy probe;
see settings.x402_listing_term_days) -- POST /list/renew no longer extends
survival at all. Repurposed 2026-09-06 (owner decision, competitive pricing
study) into a paid "boost": priority placement in search results for
settings.x402_listing_boost_days, via boosted_until_epoch. The function and
resource-constant names below stay `x402_renew` / `_RENEW_RESOURCE` even
though the resource id and behaviour are now boost's -- the ownership-check
logic renew() needs is identical to what boosting needs, so this is the
same route extended in place, not a fork (CLAUDE.md section 3).

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/list would be unreachable in production without an nginx change
this change is not authorized to deploy.

Neither paid write route (x402_list, x402_renew) accepts modules/x402/promo.py's
promo-code bypass (deliberately -- they never pass promo_code/promo_wallet into
require_paid_request). Found and closed 2026-09-04, the same pattern already
found and fixed in x402_social 2026-09-03: promo.py's own docstring says a
promo redemption's wallet is checked for SYNTACTIC validity only ("a
successful redemption is not proof the caller controls that wallet") -- fine
for a route where payer is just payment attribution, but both routes here feed
`result.payer` straight in as the wallet that OWNS the listing (create's
first-claim-wins ownership check, renew's `existing.payer != payer` check), so
a promo bypass would have let anyone claim a url "as" any wallet via
`?promo_wallet=`, either griefing that wallet with an unwanted listing
attributed to it or permanently blocking it from ever legitimately listing
that url (first-claim wins), and let anyone free-renew (or otherwise probe
the ownership check of) a listing they do not own by guessing/reading its
public payer address. There is no cheap fix that keeps promo working here
short of the same signed-challenge proof x402_social flagged needing (a real
design task, not done) -- so promo is off for these two routes until that
exists, full stop.
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
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import promo_request_params
from app.modules.x402_directory.models.domain import (
    CATEGORY_TAG_PREFIX,
    LISTING_CATEGORIES,
    DirectoryError,
    ProbeLeaderboardEntry,
    StoredListing,
    StoredProbe,
)
from app.modules.x402_directory.services.discovery_import import (
    fetch_facilitator_resources,
    import_discovered_resources,
)
from app.modules.x402_directory.services.listing_service import (
    MAX_CONTACT_LENGTH,
    MAX_SCHEMA_JSON_BYTES,
    MAX_TAG_LENGTH,
    PROBE_LEADERBOARD_MIN_SAMPLES,
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
# Repurposed 2026-09-06 from a paid-renewal-to-survive resource id into the
# new boost resource id -- see x402_renew's own docstring for why the
# Python constant/function names stay `_RENEW_RESOURCE`/`x402_renew`.
_RENEW_RESOURCE = "x402-directory-boost"
_PROBE_LEADERBOARD_RESOURCE = "x402-directory-probe-leaderboard"

# Repeated verbatim in every leaderboard response (paid, preview and the 402
# offer's own description) so the framing travels with the data everywhere
# it is read, not just in documentation a caller might not see: this ranks
# what our probe fleet MEASURED, never a paid opinion, and "most reliable
# measured so far" is not the same claim as "the best."
_PROBE_LEADERBOARD_NOTE = (
    "Ranked purely by what our probe fleet has MEASURED (reachability, valid-402 rate, "
    "latency) -- never by paid grade or spend, and nobody can pay to appear higher. "
    "'Most reliable measured so far,' not an endorsement or 'the best.' See "
    "GET /api/v1/x402/grades/top for the separate, spend-weighted agent-opinion "
    "leaderboard."
)

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
    "boosted_until_epoch": 0,
    # "paid" here (this is what a real listing looks like); a free,
    # auto-discovered stub imported from a public facilitator feed
    # (2026-09-06) carries "auto_discovered" instead -- see
    # services/discovery_import.py and _listing_json() below.
    "source": "paid",
    "discovered_from": "",
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
        # Auto-discovery (migration 113, 2026-09-06): "paid" for a real
        # listing someone actually paid to list, "auto_discovered" for a
        # free, unclaimed stub imported from a public x402 facilitator
        # discovery feed -- never ranked ahead of, or mixed indistinguishably
        # with, a paid listing (see ListingService.search()'s paid-first
        # merge). `discovered_from` names which public feed it came from;
        # empty for a real paid listing. Always present, on every listing of
        # either source, so a caller can never mistake one for the other from
        # the response shape alone.
        "source": item.source,
        "discovered_from": item.discovered_from,
        # Priority-placement window (migration 114): 0 when not boosted,
        # else the epoch until which this listing sorts ahead of every
        # non-boosted one in search() -- see ListingService.renew().
        "boosted_until_epoch": item.boosted_until_epoch,
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


def _leaderboard_entry_json(rank: int, entry: ProbeLeaderboardEntry) -> dict:
    """Serialize one probe-leaderboard row for the wire. Rounding is display-only -- the ranking itself already ran on the unrounded values."""
    return {
        "rank": rank,
        "url": entry.url,
        "verified_wallet": entry.verified_wallet,
        "sample_count": entry.sample_count,
        "uptime_pct": round(entry.uptime_pct, 2),
        "avg_latency_ms": (
            round(entry.avg_latency_ms, 1) if entry.avg_latency_ms is not None else None
        ),
        "last_probed_at_epoch": entry.last_probed_at_epoch,
    }


def x402_list(request: Request) -> Response:
    """Paid: list an x402 endpoint in the directory, surviving for as long as it stays healthy.

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
    run_with_refund grew its PlatformError exemption. No promo branch here
    -- see this module's own docstring for why.
    """
    if circuit_breaker.is_tripped(_LIST_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    term_days = settings.x402_listing_term_days
    offer = {
        "price": settings.x402_listing_price,
        "resource": _LIST_RESOURCE,
        # Reaches the payer as the 402's resource.description, before they
        # commit — the term length is not derivable from the price alone.
        "description": (
            f"List one x402 endpoint in the public PXke x402 directory. Stays listed for as "
            f"long as it keeps passing health probes (roughly every 30 minutes) -- no "
            f"renewal needed; only {term_days} days of total unresponsiveness delists it. "
            f"Discoverable immediately at GET /api/v1/x402/search (free; optional "
            f"`?tag=<tag>` filters to listings carrying that tag, `?category=<category>` "
            f"to listings in that category, `?limit=` caps results) and at "
            f"GET /api/v1/x402/listings?url=<url> (free; the full listing plus its latest "
            f"probe). POST /api/v1/x402/list/renew no longer extends survival -- it now "
            f"buys priority placement ('boost') in search results instead, see its own "
            f"402 offer. `tags` are stored trimmed and "
            f"lowercased and are what `?tag=` matches on; tags starting with "
            f"`{CATEGORY_TAG_PREFIX}` are reserved. Optional `category` is one of "
            f"{', '.join(LISTING_CATEGORIES)} (default other). Optional `schema` must "
            f"serialize to at most {MAX_SCHEMA_JSON_BYTES} bytes. Optional `reimburses` "
            f"(default false) is your own SELF-DECLARED, UNVERIFIED claim that you refund "
            f"a payer when your endpoint fails to deliver -- we do not check this for a "
            f"third-party listing, it is exactly as trustworthy as you are. Optional "
            f"`contact` (at most {MAX_CONTACT_LENGTH} characters) is a point of contact "
            f"for issues. Both are set only when you list or relist -- POST "
            f"/api/v1/x402/list/renew changes nothing about the listing's own content, "
            f"only its search-ranking boost."
        ),
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, every check below still
    # runs before the gate so a doomed request is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

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

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_LIST_RESOURCE,
        product_write=lambda: _list_product_write(
            normalized_url=normalized_url,
            payload=payload,
            tags=tags,
            schema_json=schema_json,
            result=result,
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
    category: str,
) -> dict:
    """The product write x402_list protects via run_with_refund: create the listing.

    Returns a plain dict, never a Response -- see _ping_product_write's own
    docstring in x402_catalog/api/routes.py for why a successful product
    write must never itself return a Response (the isinstance(outcome,
    Response) check the caller above relies on would become ambiguous).
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
        payer=result.payer or "",
        category=category,
        reimburses=payload.reimburses,
        contact=payload.contact,
    )
    return {"listing": _listing_json(listing)}


def x402_renew(request: Request) -> Response:
    """Paid: boost an existing listing to the top of search results, at the boost price.

    Repurposed 2026-09-06 (owner decision, competitive pricing study): this
    used to extend the listing's SURVIVAL; a listing now survives for as
    long as it keeps passing health probes (see x402_list's own docstring
    and settings.x402_listing_term_days), so this route no longer touches
    term_end_epoch at all -- see listing_service.renew() for exactly what it
    does change (boosted_until_epoch) and does not.

    Decoded, normalized and looked up BEFORE the payment gate: a malformed
    body, an invalid url and a url that is not listed are all free 400s/404s.
    Ownership cannot be checked before the gate -- the payer is only known
    once the payment has settled -- so a boost by a wallet other than the
    one that listed the url is refused with the payment already taken and
    the listing untouched (403 listing_owned_by_another_payer while the term
    runs, 409 renew_requires_relist once it has expired; receipt headers
    served either way). Same accepted tradeoff as x402_list's relist check
    and the board's own boost, and the 402 offer says so before the payer
    commits.

    The ownership refusal goes through run_with_refund (2026-09-02
    retrofit) like x402_list's identical case -- but DirectoryError is a
    PlatformError, so run_with_refund's own contract treats this exactly as
    documented above: payment kept, receipt served, NEVER a refund and
    never counted against the circuit breaker (see run_with_refund's own
    docstring, and x402_list's docstring for why refunding a fully
    caller-controlled rejection would have been a free way to pump the
    breaker). No promo branch here -- see this module's own docstring for
    why: the ownership check compares `payer` against the listing's
    existing owner, so a promo-supplied wallet would let anyone free-boost
    (or probe the ownership check of) a listing by its already-public payer
    address, without proving control of it.
    """
    if circuit_breaker.is_tripped(_RENEW_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    boost_days = settings.x402_listing_boost_days
    offer = {
        "price": settings.x402_listing_boost_price,
        "resource": _RENEW_RESOURCE,
        "description": (
            f"Boost an existing PXke x402 directory listing to the top of search results "
            f"for {boost_days} days, from the later of now and its current boost end; "
            f"nothing else about the listing changes -- not its term, price, description, "
            f"tags, schema or category. Only the wallet that listed the url may boost it, "
            f"before or after its term ends: a payment from any other wallet settles "
            f"but is refused and changes nothing (403 while the term is running, 409 "
            f"renew_requires_relist once it has expired). To take over an expired "
            f"or unowned url, POST /api/v1/x402/list to relist it under your wallet."
        ),
        "extensions": describe_json_endpoint(
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
                "boost_days": boost_days,
            },
        ),
    }
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

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

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_RENEW_RESOURCE,
        product_write=lambda: _renew_product_write(normalized_url=normalized_url, result=result),
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
                "boost_days": boost_days,
            }
        ),
    )


def _renew_product_write(*, normalized_url: str, result: PaymentResult) -> StoredListing:
    """The product write x402_renew protects via run_with_refund: boost the listing.

    Returns the renewed StoredListing directly, never a Response -- unlike
    _list_product_write, the caller wraps this return value into the final
    JSON itself (the renew response shape is simpler, just the listing), so
    there is no dict/Response ambiguity to guard against here: a StoredListing
    is never mistaken for a Response by the isinstance check the caller above
    relies on. Raises DirectoryError on the one reachable failure: a
    renewal attempt by a wallet that does not own the url
    (listing_service.renew()'s ownership check) -- the url's existence was
    already confirmed before the gate, so nothing else here can raise.
    """
    return listing_service.renew(
        normalized_url=normalized_url,
        payer=result.payer or "",
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

    With no `tag`/`category` filter, results can include free, auto-discovered
    ("unclaimed") listings imported from a public x402 facilitator feed
    (2026-09-06, `source: "auto_discovered"` on the item — see
    services/discovery_import.py) alongside real paid ones — but only to fill
    space AFTER every live paid listing already found; an auto-discovered
    entry never outranks or crowds out a paid one. A `tag`/`category` filter
    is paid-listings-only: an auto-discovered stub carries no tags.
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


def _parse_leaderboard_limit(request: Request) -> int | Response:
    """The caller's `limit`, clamped to x402_directory_probe_leaderboard_max_results; a 400 Response when non-integer."""
    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        requested = (
            int(raw_limit) if raw_limit else settings.x402_directory_probe_leaderboard_max_results
        )
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")
    return max(1, min(requested, settings.x402_directory_probe_leaderboard_max_results))


def _leaderboard_wire_shape(*, candidates_scanned: int, items: list[dict]) -> dict:
    """The response envelope shared by the real, preview and (via **outcome) final responses."""
    return {
        "basis": "measured",
        "note": _PROBE_LEADERBOARD_NOTE,
        "min_samples_required": PROBE_LEADERBOARD_MIN_SAMPLES,
        "sample_window": settings.x402_probe_history_max_results,
        "candidates_scanned": candidates_scanned,
        "items": items,
    }


def x402_probe_leaderboard(request: Request) -> Response:
    """Paid: the most reliable listed x402 endpoints, ranked by probe-MEASURED data (roadmap item 7's trust-layer step 3).

    Complementary to, and deliberately distinct from, x402_grading's paid
    leaderboards: grading sells a spend-weighted AGENT OPINION (sybil-
    vulnerable -- enough wallets grading small amounts can buy a rank), this
    sells pure MACHINE MEASUREMENT from the probe fleet that already runs
    against every listing every 30 minutes. Nobody can pay their way onto
    this one, and the response says so (see _PROBE_LEADERBOARD_NOTE) rather
    than ever claiming an endpoint is "the best."

    An empty or all-too-new directory is a valid, chargeable, honestly-empty
    answer (`items: []`, `candidates_scanned` says how many live listings
    were actually measured) -- NOT a 404 the way x402_grading's per-tag
    leaderboard pre-gate-refuses an empty one. That route can cheaply check
    "does this ONE tag have a rankable candidate" for free before the gate;
    this one ranks the WHOLE directory, so the only free pre-check available
    would be "is the directory nonempty at all," which does not predict
    whether anything clears the reliability threshold -- see
    ListingService.probe_leaderboard()'s own docstring for the ranking rule
    and its minimum-sample-count anti-gaming threshold.

    An unpaid request sees the offer before `limit` is ever parsed (see
    challenge_if_unpaid) -- found live 2026-09-06, the same bug class as
    x402_list/x402_renew: a bare header-less probe with a malformed `limit`
    got a 400 and never saw the price. With a payment attached, `limit` is
    still parsed and clamped before the gate as before (a non-integer is a
    free 400). Supports `?preview=true` (redacted shape, unpaid,
    rate-limited) and an admin promo bypass -- this is a read-only aggregate
    with no ownership check to game, unlike x402_list/x402_renew's
    promo-unwired write paths (see this module's own docstring).
    """
    if circuit_breaker.is_tripped(_PROBE_LEADERBOARD_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    offer = {
        "price": settings.x402_directory_probe_leaderboard_price,
        "resource": _PROBE_LEADERBOARD_RESOURCE,
        "description": (
            "The most reliable listed x402 endpoints, ranked purely by what our probe "
            f"fleet has MEASURED -- reachability, valid-402 rate and latency -- over each "
            f"listing's most recent up to {settings.x402_probe_history_max_results} stored "
            f"probes (30-minute cadence). Never by paid grade or spend: nobody can pay to "
            f"appear higher. A listing needs at least {PROBE_LEADERBOARD_MIN_SAMPLES} "
            f"probes in that window to be ranked at all, so a brand-new listing cannot "
            f"buy a top spot with one lucky probe. This is a measurement, not an "
            f"endorsement -- 'most reliable measured so far,' never 'the best.' See "
            f"GET /api/v1/x402/grades/top for the separate, spend-weighted opinion "
            f"leaderboard. Optional `limit` (clamped to "
            f"{settings.x402_directory_probe_leaderboard_max_results}). Supports "
            f"?preview=true (redacted, unpaid, rate-limited)."
        ),
        "extensions": describe_json_endpoint(
            input={"limit": 10},
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
            },
            output_example=_leaderboard_wire_shape(
                candidates_scanned=2,
                items=[
                    {
                        "rank": 1,
                        "url": _LISTING_EXAMPLE["url"],
                        "verified_wallet": "",
                        "sample_count": 60,
                        "uptime_pct": 98.33,
                        "avg_latency_ms": 145.2,
                        "last_probed_at_epoch": 0,
                    }
                ],
            )
            | {"settlement_tx_id": "..."},
        ),
    }
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    limit = _parse_leaderboard_limit(request)
    if isinstance(limit, Response):
        return limit

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        # Shape, not values: probe_leaderboard() (the real ranking) is never
        # called for a preview caller -- the order itself is part of what
        # this route sells, not just the numbers. candidates_scanned=-1 is
        # an unambiguous sentinel: a real response can never legitimately
        # carry a negative scan count.
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {
                    **_leaderboard_wire_shape(
                        candidates_scanned=-1,
                        items=[
                            {
                                "rank": 1,
                                "url": "<preview>",
                                "verified_wallet": "<preview>",
                                "sample_count": -1,
                                "uptime_pct": 0.0,
                                "avg_latency_ms": None,
                                "last_probed_at_epoch": 0,
                            }
                        ],
                    ),
                    "settlement_tx_id": "<preview>",
                }
            ),
        )

    def _build_leaderboard() -> dict:
        entries, candidates_scanned = listing_service.probe_leaderboard(limit=limit)
        return _leaderboard_wire_shape(
            candidates_scanned=candidates_scanned,
            items=[
                _leaderboard_entry_json(rank, entry) for rank, entry in enumerate(entries, start=1)
            ],
        )

    outcome = run_with_refund(
        result,
        resource=_PROBE_LEADERBOARD_RESOURCE,
        product_write=_build_leaderboard,
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    if not result.is_promo:
        mark_fulfilled(result.payment_txid, resource=_PROBE_LEADERBOARD_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                **outcome,
                "settlement_tx_id": result.payment_txid or "",
                **({"via": "promo"} if result.is_promo else {}),
            }
        ),
    )


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


def x402_admin_import_discovered_listings(request: Request) -> Response | dict:
    """Admin: fetch the facilitator's public discovery feed and import/refresh auto-discovered listings.

    One-off/on-demand trigger, not a scheduled beat (this backend has no
    Celery scheduler of its own -- see discovery_import.py's own docstring
    for the deliberately smaller scope). Fetches
    GET {settings.x402_facilitator_url}discovery/resources for the
    configured network and writes one FREE, clearly-labelled auto-discovered
    listing per resource (StoredListing.source == SOURCE_AUTO_DISCOVERED),
    never touching a url a real payer already listed
    (import_discovered_resources()'s own skipped_existing_paid count). Safe
    to call repeatedly: each call only creates rows for urls that don't yet
    have one, or refreshes (term_end, price, description) rows this same
    import mechanism created earlier.

    Never charges anything and never calls anything on the facilitator that
    settles or mutates state -- one GET, paginated, bounded (see
    fetch_facilitator_resources()). A facilitator-side failure is reported in
    the response's `fetch.error`, never raised as a 500.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    fetch_result = fetch_facilitator_resources()
    # Explicit store=, not the parameter's own get_listing_store() default:
    # `listing_service.store` is the same module-level singleton (or,
    # in a test, the same injected fake) every other route on this module
    # reads and writes through -- see ListingService.store's own docstring.
    import_result = import_discovered_resources(fetch_result.resources, store=listing_service.store)
    return {
        "fetch": {
            "scanned": len(fetch_result.resources),
            "total_reported_by_facilitator": fetch_result.total_reported,
            "error": fetch_result.error,
        },
        "import": {
            "scanned": import_result.scanned,
            "created": import_result.created,
            "refreshed": import_result.refreshed,
            "skipped_existing_paid": import_result.skipped_existing_paid,
            "skipped_invalid": import_result.skipped_invalid,
        },
    }


def register_x402_directory_routes(app: Router) -> None:
    """Register the paid list/renew/probe-leaderboard routes, the free search/detail/probe-status/probe-history routes, and the admin delist/import-discovered routes.

    x402-marketplace-ux-audit.md section 3.3 found five unrelated path
    prefixes for one product (N1) and `renew` meaning "boost" here while
    meaning something else in storage (N3). Each renamed path below is a
    second, direct route registration against the SAME handler as its old
    path -- never a duplicate body, never an HTTP-internal redirect. The old
    paths stay registered exactly as they are: this marketplace is live on
    mainnet and an existing caller's hardcoded path must keep working
    (section 3.5 "Migration without breakage").
    """
    app.post("/api/v1/x402/list")(x402_list)
    app.post("/api/v1/x402/directory/listings")(x402_list)
    app.post("/api/v1/x402/list/renew")(x402_renew)
    app.post("/api/v1/x402/directory/listings/boost")(x402_renew)
    app.get("/api/v1/x402/search")(x402_search)
    app.get("/api/v1/x402/directory/listings")(x402_search)
    app.get("/api/v1/x402/listings")(x402_listing_detail)
    app.get("/api/v1/x402/directory/listings/lookup")(x402_listing_detail)
    app.get("/api/v1/x402/directory/probe")(x402_probe_status)
    app.get("/api/v1/x402/uptime/probes/latest")(x402_probe_status)
    app.get("/api/v1/x402/directory/probe/history")(x402_probe_history)
    app.get("/api/v1/x402/uptime/probes")(x402_probe_history)
    app.get("/api/v1/x402/directory/probe/leaderboard")(x402_probe_leaderboard)
    app.get("/api/v1/x402/trust/leaderboards/reliability")(x402_probe_leaderboard)
    app.delete("/api/v1/admin/x402/listings")(x402_admin_delete_listing)
    app.post("/api/v1/admin/x402/directory/import-discovered")(
        x402_admin_import_discovered_listings
    )
