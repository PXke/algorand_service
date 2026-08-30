"""Directory listing rules: URL normalization, term computation, storage."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from app.core import serialization
from app.core.config import settings
from app.modules.x402_directory.models.domain import DirectoryError, StoredListing
from app.modules.x402_directory.stores.base import ListingStore
from app.modules.x402_directory.stores.factory import get_listing_store

_ALLOWED_SCHEMES = ("http", "https")
_MAX_URL_LENGTH = 2048


def normalize_url(raw: str) -> str:
    """Normalize an endpoint URL to the canonical form the directory keys on.

    Lowercases the scheme and host (both case-insensitive per RFC 3986) and
    drops the fragment, which is never sent to a server and so cannot identify a
    distinct endpoint. The path, query and any explicit port are left exactly as
    given: those ARE case- and content-significant, and rewriting them could
    point a listing at a different resource than the payer paid to list.
    """
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_URL_LENGTH:
        raise DirectoryError("invalid_request", "url must be 1-2048 characters")
    parts = urlsplit(trimmed)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise DirectoryError("invalid_request", "url must be http or https")
    if not parts.hostname:
        raise DirectoryError("invalid_request", "url must include a host")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def url_hash(normalized_url: str) -> str:
    """Partition key for a listing: a hex SHA-256 of the normalized URL.

    Hashed rather than using the URL itself so the partition key is a fixed,
    bounded length regardless of how long the listed URL is.
    """
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


class ListingService:
    """Creates and reads directory listings."""

    def __init__(self, store: ListingStore | None = None) -> None:
        """Take an explicit store for tests; otherwise resolve the configured one lazily."""
        self._store = store

    @property
    def store(self) -> ListingStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_listing_store()

    def create(
        self,
        *,
        url: str,
        price: str,
        description: str,
        assets: list[str],
        tags: list[str],
        schema: dict | None,
        settlement_tx_id: str,
        payer: str,
        now: datetime | None = None,
    ) -> StoredListing:
        """Store a paid listing for the configured term and return it.

        Re-listing a URL already in the directory replaces it and re-stamps both
        created_at and term_end: the payer paid for a fresh term starting now,
        not for an extension of whatever the previous term was.

        Ownership check (migration 094): if this url already has a listing
        owned by a DIFFERENT non-empty payer, this raises rather than
        overwriting. A directory entry states a fact about a real third-party
        endpoint -- letting any payer take over any listing by paying the fee
        would let anyone quietly misrepresent someone else's endpoint. An
        empty existing payer (pre-migration data) is unowned and gets claimed
        by whoever relists it next, so old listings aren't locked forever.

        This check runs AFTER payment already settled (the route already
        collected it before calling create()), so a blocked hijack attempt
        still costs the attacker the listing fee -- named plainly rather than
        hidden, the same tradeoff x402_grading's eligibility check accepted
        for the same reason: payer identity isn't known until settlement.

        Deliberately no `and payer` guard on the new side: an unattributable
        new payer (empty string) must NOT be able to overwrite an existing
        OWNED listing just because its own identity is unknown -- that would
        turn "we couldn't attribute this payment" into a free bypass of the
        exact check this exists for. An unattributable payer can still create
        a brand-new listing (existing is None) or claim an unowned one
        (existing.payer == ""), same as any other payer.
        """
        moment = now or datetime.now(tz=UTC)
        normalized = normalize_url(url)
        key = url_hash(normalized)
        existing = self.store.get(key)
        if existing is not None and existing.payer and existing.payer != payer:
            raise DirectoryError(
                "listing_owned_by_another_payer",
                "This url is already listed by a different payer. Payment has "
                "settled but the existing listing was not changed.",
            )
        listing = StoredListing(
            url_hash=key,
            url=normalized,
            price=price,
            description=description.strip(),
            schema_json=serialization.dumps(schema) if schema else "",
            settlement_tx_id=settlement_tx_id,
            term_end_epoch=int(
                (moment + timedelta(days=settings.x402_listing_term_days)).timestamp()
            ),
            created_at_epoch=int(moment.timestamp()),
            assets=sorted({a.strip() for a in assets if a.strip()}),
            tags=sorted({t.strip().lower() for t in tags if t.strip()}),
            payer=payer,
        )
        self.store.upsert(listing)
        return listing

    def search(self, *, limit: int) -> list[StoredListing]:
        """Return listings newest-first, clamped to the configured maximum."""
        clamped = max(1, min(limit, settings.x402_search_max_results))
        return self.store.list_recent(limit=clamped)
