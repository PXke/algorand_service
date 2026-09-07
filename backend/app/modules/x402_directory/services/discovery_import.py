"""Import auto-discovered ("unclaimed") directory listings from a public x402 facilitator feed.

The directory product's flagship pitch is "pay to list, pay for ranked
search, free browse," but in practice it looked empty: 2 real paid listings
and nothing else, next to GoPlausible's own facilitator, which already
publishes a large public catalog of live Algorand-network x402 resources
(`GET {settings.x402_facilitator_url}discovery/resources`). This module
republishes that ALREADY-PUBLIC information as free, clearly labelled,
unclaimed listings (`StoredListing.source == SOURCE_AUTO_DISCOVERED`) so the
directory reads as a populated ecosystem index instead of an empty shell.

Zero wash-volume exposure by construction (CLAUDE.md section 9): no payment
is made, required, or simulated anywhere in this path, nothing here settles
against our own facilitator account or pays our own endpoints, and an
imported row can never be mistaken for a real paid listing -- see
`StoredListing.source` and `ListingService.search()`'s paid-first merge,
which never lets an auto-discovered row outrank or crowd out a real one.

Two functions, split so the regression tests can exercise the pure import
logic without ever touching the network (CLAUDE.md section 6: tests are
no-network by design):

* `fetch_facilitator_resources()` -- the one network call, paginated, never
  raising for a network-side failure (mirrors `x402_uptime.checker.check_target()`
  and `x402.price_oracle`'s own "degrade, don't crash" contract for an
  external, not-ours dependency).
* `import_discovered_resources()` -- pure: takes already-fetched resource
  dicts and writes `StoredListing` rows through the same `ListingStore`
  paid listings use. This is what the regression tests call directly.

No Celery beat is wired here: this ships as a plain callable plus an
admin-triggered route (`POST /api/v1/admin/x402/directory/import-discovered`
in `api/routes.py`), the safer, smaller scope for now -- see this feature's
own task notes. An operator (or a later beat, if one is added) re-runs the
import periodically to refresh `term_end_epoch` and pick up newly-published
resources; nothing here schedules that automatically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from app.core.config import settings
from app.modules.x402_directory.models.domain import (
    DEFAULT_CATEGORY,
    SOURCE_AUTO_DISCOVERED,
    DirectoryError,
    StoredListing,
)
from app.modules.x402_directory.services.listing_service import normalize_url, url_hash
from app.modules.x402_directory.stores.base import ListingStore
from app.modules.x402_directory.stores.factory import get_listing_store

logger = logging.getLogger(__name__)

# Attribution recorded on every imported row (StoredListing.discovered_from)
# -- this is republishing already-public facilitator data, always labelled
# where it came from, never presented as though we discovered or verified it
# ourselves.
DISCOVERY_SOURCE_LABEL = "goplausible_facilitator"

# One page of the facilitator's own `limit`/`offset` pagination. Well under
# any sane server-side cap -- this is a courtesy to the facilitator's own
# service, not something it told us to use.
_PAGE_SIZE = 500
# Hard ceiling on total resources fetched in one run, independent of what the
# facilitator's own `pagination.total` claims -- CLAUDE.md section 4's "no
# unbounded listings" applies to what WE fetch just as much as to what we
# serve; a runaway or hostile total must not turn one admin-triggered import
# into an unbounded fetch loop.
_MAX_TOTAL_FETCH = 5000
_HTTP_TIMEOUT_SECONDS = 10.0
# Same bounds X402ListingRequest already enforces on a real paid listing's
# own price/description, applied here so an oversized facilitator field
# cannot turn into an oversized free response later (GET /x402/search serves
# every listing, of either source, back inline).
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_PRICE_LENGTH = 64


@dataclass(frozen=True)
class FacilitatorFetchResult:
    """One `fetch_facilitator_resources()` call's outcome -- always returned, never raised.

    `error` is non-empty exactly when the fetch stopped early because of a
    network-side failure (as opposed to a legitimately short or empty feed):
    the caller can tell "the facilitator reported zero live resources" apart
    from "we couldn't reach the facilitator," the same "empty is not none
    found" distinction CLAUDE.md section 2 states for a tool's own result.
    """

    resources: list[dict]
    total_reported: int
    error: str


def fetch_facilitator_resources(
    *, transport: httpx.BaseTransport | None = None
) -> FacilitatorFetchResult:
    """Paginate `GET {x402_facilitator_url}discovery/resources` for the configured network.

    Read-only: a GET against the facilitator's own public discovery feed,
    never a call that settles, pays, or mutates anything on either side.
    Stops at the facilitator's own `pagination.total`, or at
    `_MAX_TOTAL_FETCH`, whichever is smaller -- always a bounded number of
    pages. Never raises for a network-side failure (timeout, non-2xx,
    malformed body): returns whatever was collected so far plus a non-empty
    `error`, the same "degrade, don't crash" contract `price_oracle.py` and
    `x402_uptime/services/checker.py` already use for an external dependency
    we don't operate.

    `transport` is a test seam only (`httpx.MockTransport`), never passed in
    production code.
    """
    base = settings.x402_facilitator_url.rstrip("/")
    resources: list[dict] = []
    total_reported = 0
    offset = 0
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, transport=transport) as client:
            while len(resources) < _MAX_TOTAL_FETCH:
                response = client.get(
                    f"{base}/discovery/resources",
                    params={
                        "network": settings.x402_network,
                        "limit": _PAGE_SIZE,
                        "offset": offset,
                    },
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    return FacilitatorFetchResult(
                        resources=resources,
                        total_reported=total_reported,
                        error="facilitator returned a non-object response",
                    )
                page = body.get("items")
                if not isinstance(page, list):
                    return FacilitatorFetchResult(
                        resources=resources,
                        total_reported=total_reported,
                        error="facilitator response is missing an `items` array",
                    )
                pagination = body.get("pagination")
                if isinstance(pagination, dict):
                    try:
                        total_reported = int(pagination.get("total") or 0)
                    except (TypeError, ValueError):
                        total_reported = total_reported
                resources.extend(item for item in page if isinstance(item, dict))
                if len(page) < _PAGE_SIZE:
                    break
                offset += _PAGE_SIZE
                if total_reported and offset >= total_reported:
                    break
    except Exception as exc:
        # Logged with context (the class name only -- CLAUDE.md section 4:
        # never return str(exc) to a caller) at warning, per CLAUDE.md
        # section 3's "no new except Exception: pass": this degrades to a
        # partial/empty result rather than crashing the admin route, and the
        # caller can distinguish it from a genuinely empty feed via `error`.
        logger.warning(
            "x402 directory auto-discovery fetch failed after %d resources: %s",
            len(resources),
            exc,
        )
        return FacilitatorFetchResult(
            resources=resources,
            total_reported=total_reported,
            error=exc.__class__.__name__,
        )
    return FacilitatorFetchResult(
        resources=resources[:_MAX_TOTAL_FETCH], total_reported=total_reported, error=""
    )


@dataclass(frozen=True)
class DiscoveryImportResult:
    """One `import_discovered_resources()` call's outcome."""

    scanned: int
    created: int
    refreshed: int
    skipped_existing_paid: int
    skipped_invalid: int


def _resource_price(accepts: object) -> str:
    """A short, informational price string for one facilitator resource's first `accepts` entry.

    Never used for payment -- this whole import path takes no payment at all
    (see module docstring) -- purely descriptive, the same "informational,
    not actionable" spirit as a probe's own reported latency.
    """
    if not isinstance(accepts, list) or not accepts or not isinstance(accepts[0], dict):
        return ""
    entry = accepts[0]
    amount = entry.get("amount")
    asset = entry.get("asset", "")
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    if amount is None:
        return ""
    decimals = extra.get("decimals")
    if isinstance(decimals, int) and not isinstance(decimals, bool) and decimals >= 0:
        try:
            value = Decimal(str(amount)) / (Decimal(10) ** decimals)
        except (InvalidOperation, ValueError):
            value = None
        if value is not None:
            label = extra.get("name") or f"asset {asset}"
            return f"~{value.normalize()} {label}"[:_MAX_PRICE_LENGTH]
    return f"{amount} (asset {asset})"[:_MAX_PRICE_LENGTH]


def _resource_description(resource: dict) -> str:
    """Method-prefixed, length-bounded description for one facilitator resource."""
    method = str(resource.get("method") or "").upper().strip()
    text = str(resource.get("description") or "").strip()
    prefixed = f"[{method}] {text}" if method else text
    return prefixed[:_MAX_DESCRIPTION_LENGTH]


def import_discovered_resources(
    resources: list[dict],
    *,
    store: ListingStore | None = None,
    now: datetime | None = None,
) -> DiscoveryImportResult:
    """Write one auto-discovered StoredListing per resource, never touching a real paid listing.

    Idempotent and safe to re-run: a url already listed by a real payer
    (`StoredListing.source == SOURCE_PAID`) is always skipped
    (`skipped_existing_paid`), whatever the facilitator says about it now --
    an already-public catalog entry is informational, never grounds to
    overwrite something someone actually paid us to list. A url with no
    listing yet, or one whose existing listing is itself a previous
    auto-discovered import, is written (created or refreshed in place).

    `created_at_epoch` is preserved across a refresh -- an unclaimed listing
    does not jump to the front of the free feed just because the
    facilitator's own record was re-fetched; only a genuinely NEW url gets
    today's timestamp. `term_end_epoch` is always re-stamped to
    `now + x402_directory_auto_discovered_term_days`, the same way a paid
    renewal extends term_end, so a url the facilitator keeps reporting stays
    visible for as long as it keeps being re-imported.

    A resource missing a usable url, or one whose url fails the same
    `normalize_url()` validation a real listing goes through, is skipped
    (`skipped_invalid`) rather than raising -- one malformed entry in a feed
    of over a thousand must not abort the whole import.
    """
    active_store = store or get_listing_store()
    moment = now or datetime.now(tz=UTC)
    term_end_epoch = int(
        (moment + timedelta(days=settings.x402_directory_auto_discovered_term_days)).timestamp()
    )
    created = refreshed = skipped_existing_paid = skipped_invalid = 0
    for resource in resources:
        raw_url = resource.get("resourceUrl") or resource.get("url") or ""
        try:
            normalized = normalize_url(str(raw_url))
        except DirectoryError:
            skipped_invalid += 1
            continue
        key = url_hash(normalized)
        existing = active_store.get(key)
        if existing is not None and not existing.is_auto_discovered:
            skipped_existing_paid += 1
            continue
        listing = StoredListing(
            url_hash=key,
            url=normalized,
            price=_resource_price(resource.get("accepts")),
            description=_resource_description(resource),
            schema_json="",
            settlement_tx_id="",
            term_end_epoch=term_end_epoch,
            created_at_epoch=(
                existing.created_at_epoch if existing is not None else int(moment.timestamp())
            ),
            assets=[],
            tags=[],
            payer="",
            category=DEFAULT_CATEGORY,
            source=SOURCE_AUTO_DISCOVERED,
            discovered_from=DISCOVERY_SOURCE_LABEL,
        )
        active_store.upsert(listing)
        if existing is None:
            created += 1
        else:
            refreshed += 1
    return DiscoveryImportResult(
        scanned=len(resources),
        created=created,
        refreshed=refreshed,
        skipped_existing_paid=skipped_existing_paid,
        skipped_invalid=skipped_invalid,
    )


__all__ = [
    "DISCOVERY_SOURCE_LABEL",
    "DiscoveryImportResult",
    "FacilitatorFetchResult",
    "fetch_facilitator_resources",
    "import_discovered_resources",
]
