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

# Cap on one listing's serialized request schema. Deliberately far below the
# 256 KiB global body cap (core/falcon_router.py): a listing is paid input that
# GET /x402/search serves back inline, for free, up to
# settings.x402_search_max_results (100) at a time, so the body cap alone would
# put a ~25 MB free response one paid listing at a time within reach. At 4 KiB
# -- twice this struct's largest free-text bound, `description` at 2000 -- a
# full 100-listing search response stays around 1 MB. It is also generous for
# what the field is for: a JSON Schema describing one endpoint's input.
MAX_SCHEMA_JSON_BYTES = 4096


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


def encode_schema(schema: dict | None) -> str:
    """Serialize a listing's request schema to the stored JSON string, size-capped.

    Raises invalid_request if the encoded form exceeds MAX_SCHEMA_JSON_BYTES.
    The route calls this BEFORE the payment gate, so an oversized schema is a
    400 that nobody is charged for; create() takes the string this returns
    rather than the dict, so the encoding and the size check happen exactly
    once on the paid path.
    """
    if not schema:
        return ""
    encoded = serialization.dumps(schema)
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_JSON_BYTES:
        raise DirectoryError(
            "invalid_request",
            f"schema must serialize to at most {MAX_SCHEMA_JSON_BYTES} bytes",
        )
    return encoded


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
        normalized_url: str,
        price: str,
        description: str,
        assets: list[str],
        tags: list[str],
        schema_json: str,
        settlement_tx_id: str,
        payer: str,
        now: datetime | None = None,
    ) -> StoredListing:
        """Store a paid listing for the configured term and return it.

        Takes the URL ALREADY normalized (normalize_url) and the schema ALREADY
        encoded (encode_schema), because both of those can reject a request and
        both must therefore run before the payment gate, not here -- see the
        route's docstring. Doing them again here would be doing paid-path work
        twice; the guards below only re-assert what those two produce.

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
        if not normalized_url.strip():
            # normalize_url cannot return this, so the route cannot reach it.
            # Kept so a future caller cannot store a listing keyed on nothing.
            raise DirectoryError("invalid_request", "url must include a host")
        if len(schema_json.encode("utf-8")) > MAX_SCHEMA_JSON_BYTES:
            # Likewise unreachable from the route, which calls encode_schema()
            # before the gate. This is the durable guard on the column: a
            # future caller must not be able to store an unbounded blob that
            # the free search then serves back inline.
            raise DirectoryError(
                "invalid_request",
                f"schema must serialize to at most {MAX_SCHEMA_JSON_BYTES} bytes",
            )
        key = url_hash(normalized_url)
        existing = self.store.get(key)
        if existing is not None and existing.payer and existing.payer != payer:
            raise DirectoryError(
                "listing_owned_by_another_payer",
                "This url is already listed by a different payer. Payment has "
                "settled but the existing listing was not changed.",
            )
        listing = StoredListing(
            url_hash=key,
            url=normalized_url,
            price=price,
            description=description.strip(),
            schema_json=schema_json,
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

    def search(self, *, limit: int, now: datetime | None = None) -> list[StoredListing]:
        """Return listings whose term is still running, newest-first, clamped.

        The name stays `search` rather than becoming `search_active`: for a
        search endpoint, live results are what a caller already expects, and it
        is returning EXPIRED ones that would need announcing.

        A listing whose term has ended must stop being served: the 402 offer
        sells "List one x402 endpoint ... for N days" and one payment bought
        one term, not permanent presence in the directory. Before this filter
        existed, nothing on the read path looked at term_end_epoch at all, so a
        single payment listed a URL forever.

        Mirrors x402_board's BoardService.list_active(), including its
        tradeoff: expired rows are dropped HERE rather than in each store, so
        the rule applies identically to Cassandra and memory, and the filter
        runs after the LIMITed read rather than as a CQL predicate -- so a page
        can come back short when the front of the feed is full of expired
        listings. Accepted for the same reason it is accepted there: the feed
        is a single bounded partition (DIRECTORY_PARTITION) and every read
        stays LIMITed, whereas filtering in CQL on a non-key column would mean
        ALLOW FILTERING, which CLAUDE.md section 4 forbids. A Cassandra TTL on
        the projection, or a sweep, is the real fix and is not built here.
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_search_max_results))
        return [
            item for item in self.store.list_recent(limit=clamped) if item.term_end_epoch > cutoff
        ]
