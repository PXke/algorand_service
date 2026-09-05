"""Directory listing rules: URL normalization, term computation, storage."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from app.core import serialization
from app.core.config import settings
from app.modules.x402_directory.models.domain import (
    CATEGORY_TAG_PREFIX,
    DEFAULT_CATEGORY,
    LISTING_CATEGORIES,
    DirectoryError,
    ProbeLeaderboardEntry,
    StoredListing,
    StoredProbe,
    category_tag,
)
from app.modules.x402_directory.stores.base import ListingStore
from app.modules.x402_directory.stores.factory import get_listing_store

_ALLOWED_SCHEMES = ("http", "https")
_MAX_URL_LENGTH = 2048

# Per-tag length bound, matching X402ListingRequest.tags' per-item bound: a
# search tag longer than any storable tag cannot match anything, and bounding
# it keeps a free query param from carrying an arbitrarily long partition key.
MAX_TAG_LENGTH = 64

# Bound on the self-declared `contact` field (migration 104). Not shape-
# validated (an email, a URL, an agent identifier are all legitimate) --
# only bounded, same reasoning as description's 2000-char cap: paid input
# served back for free by search()/detail(), so an unbounded field here is
# an unbounded free response at our egress cost. 256 comfortably covers any
# real email or URL without inviting abuse as a second free-text dumping
# ground.
MAX_CONTACT_LENGTH = 256

# Cap on one listing's serialized request schema. Deliberately far below the
# 256 KiB global body cap (core/falcon_router.py): a listing is paid input that
# GET /x402/search serves back inline, for free, up to
# settings.x402_search_max_results (100) at a time, so the body cap alone would
# put a ~25 MB free response one paid listing at a time within reach. At 4 KiB
# -- twice this struct's largest free-text bound, `description` at 2000 -- a
# full 100-listing search response stays around 1 MB. It is also generous for
# what the field is for: a JSON Schema describing one endpoint's input.
MAX_SCHEMA_JSON_BYTES = 4096

# Minimum stored probes a listing needs, within its own sample window, before
# probe_leaderboard() ranks it at all (roadmap item 7, the probe-MEASURED
# trust leaderboard). At the probe beat's 30-minute cadence this is roughly
# a day of consistent probing -- enough that a listing minutes old cannot
# buy a top rank with one lucky probe, while a genuinely-new-but-reliable
# listing still qualifies within its first day rather than having to wait
# weeks. Tunable; not derived from anything else. Same shape as grading's
# own MIN_LEADERBOARD_GRADES threshold, for the equivalent anti-gaming reason.
PROBE_LEADERBOARD_MIN_SAMPLES = 48

# How many live listings probe_leaderboard() scans as ranking candidates,
# newest-created first (the same recency feed search() reads). Bounded
# (CLAUDE.md section 4) and deliberately smaller than x402_search_max_results:
# each candidate here costs one more bounded probe_history read on top of the
# listing read itself, so the whole route's Cassandra cost scales with this
# number, not with the directory's real size. Same role as grading's own
# TOP_CANDIDATE_LIMIT.
PROBE_LEADERBOARD_CANDIDATE_LIMIT = 50


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


def normalize_tag(raw: str) -> str:
    """Canonical stored/searched form of one tag: trimmed and lowercased.

    The ONE normalization both sides use -- create() runs every submitted tag
    through it before storing, and search() runs the `tag` query param
    through it before reading the by-tag projection -- so "FX", " fx " and
    "fx" all name the same partition. Returns "" for a blank tag, which
    create() drops and search() rejects.
    """
    return raw.strip().lower()


def search_tag(raw: str) -> str:
    """Validate and normalize the `tag` search filter, raising invalid_request if unusable.

    The reserved `category:` namespace is refused here too: a category is
    searched with `?category=`, and letting `?tag=category:x` read the same
    partition would make the reserved prefix a second, undocumented API.
    """
    tag = normalize_tag(raw)
    if not tag or len(tag) > MAX_TAG_LENGTH:
        raise DirectoryError("invalid_request", f"tag must be 1-{MAX_TAG_LENGTH} characters")
    if tag.startswith(CATEGORY_TAG_PREFIX):
        raise DirectoryError(
            "invalid_request", f"tags starting with `{CATEGORY_TAG_PREFIX}` are reserved"
        )
    return tag


def validate_tags(tags: list[str]) -> list[str]:
    """Normalize a listing's submitted tags, refusing any in the reserved `category:` namespace.

    Runs BEFORE the payment gate (the route calls it next to normalize_url),
    so a forged category tag is a 400 nobody pays for. Blank tags are
    dropped, duplicates folded, the result sorted -- the stored form.
    """
    normalized = sorted({normalize_tag(t) for t in tags if normalize_tag(t)})
    for tag in normalized:
        if tag.startswith(CATEGORY_TAG_PREFIX):
            raise DirectoryError(
                "invalid_request",
                f"tag `{tag}` is reserved: tags starting with `{CATEGORY_TAG_PREFIX}` "
                f"are written by the directory itself from `category`",
            )
    return normalized


def validate_category(raw: str) -> str:
    """Canonical form of a listing's category, raising invalid_request if it is not in the enum.

    Trimmed and lowercased; blank means DEFAULT_CATEGORY. Runs BEFORE the
    payment gate, so an unknown category is a free 400, and again on the
    `?category=` search filter so both sides name the same partition.
    """
    category = raw.strip().lower() or DEFAULT_CATEGORY
    if category not in LISTING_CATEGORIES:
        raise DirectoryError(
            "invalid_request",
            f"category must be one of: {', '.join(LISTING_CATEGORIES)}",
        )
    return category


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
        category: str = DEFAULT_CATEGORY,
        reimburses: bool = False,
        contact: str = "",
        now: datetime | None = None,
    ) -> StoredListing:
        """Store a paid listing for the configured term and return it.

        Takes the URL ALREADY normalized (normalize_url) and the schema ALREADY
        encoded (encode_schema), because both of those can reject a request and
        both must therefore run before the payment gate, not here -- see the
        route's docstring. Doing them again here would be doing paid-path work
        twice; the guards below only re-assert what those two produce. The
        same holds for `category` (validate_category) and `tags`
        (validate_tags): both run pre-gate in the route, and are re-run here
        only as the durable guard on the columns.

        Re-listing a URL already in the directory replaces it and re-stamps both
        created_at and term_end: the payer paid for a fresh term starting now,
        not for an extension of whatever the previous term was.

        Ownership check (migration 094): if this url already has a listing
        owned by a DIFFERENT non-empty payer AND that listing's paid term has
        not yet ended, this raises rather than overwriting. A directory entry
        states a fact about a real third-party endpoint -- letting any payer
        take over any listing by paying the fee would let anyone quietly
        misrepresent someone else's endpoint. An empty existing payer
        (pre-migration data) is unowned and gets claimed by whoever relists it
        next, so old listings aren't locked forever -- and the same must hold
        for a listing whose term_end_epoch has already passed: the payer only
        bought protection for the term they paid for, not forever, so an
        expired listing is unowned the same way an empty-payer one is. Without
        this, one $0.10 listing fee would permanently squat a url against
        every future payer, long after the directory has stopped showing it
        (search() already excludes expired listings from results).

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

        Write ordering: the first-time path is the store's atomic
        insert_if_absent (a Cassandra lightweight transaction), NOT a read
        followed by an upsert. Two concurrent first-time listers of one url
        would both read "not listed", both pass the ownership check, and the
        last upsert would silently discard the other payer's paid listing.
        With the conditional insert exactly one of them creates the row; the
        other gets False, re-reads the winner's row and is held to the same
        ownership rule as any relist -- refused if the winner is a different
        live payer, otherwise allowed through the relist path. The relist
        path (same owner, unowned, or expired) is a plain upsert.

        Verified badge (097): every store write persists verified_wallet /
        verified_at on the canonical row and both projections, so a relist by
        the SAME payer carries the existing badge through (the endpoint's
        payTo has not been re-checked, but the wallet it attests to is
        unchanged), while a relist that changes the payer writes the badge
        empty everywhere -- the previous owner's attestation must not sit in
        storage under the new owner's name, whatever is_verified would say.
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
            tags=validate_tags(tags),
            payer=payer,
            category=validate_category(category),
            reimburses=reimburses,
            contact=contact.strip(),
        )
        if self.store.insert_if_absent(listing):
            return listing
        # Lost the first-insert (or the url was already listed): apply the
        # ownership rule against whatever is there now.
        existing = self.store.get(key)
        existing_is_owned = (
            existing is not None
            and existing.payer
            and existing.payer != payer
            and existing.term_end_epoch > int(moment.timestamp())
        )
        if existing_is_owned:
            raise DirectoryError(
                "listing_owned_by_another_payer",
                "This url is already listed by a different payer. Payment has "
                "settled but the existing listing was not changed.",
            )
        if existing is not None and existing.payer and existing.payer == payer:
            listing = replace(
                listing,
                verified_wallet=existing.verified_wallet,
                verified_at_epoch=existing.verified_at_epoch,
            )
        self.store.upsert(listing)
        return listing

    def search(
        self,
        *,
        limit: int,
        tag: str | None = None,
        category: str | None = None,
        now: datetime | None = None,
    ) -> list[StoredListing]:
        """Return listings whose term is still running, newest-first, clamped.

        With `tag` (raw, as received -- normalized here with the same rule
        create() stores tags under), only listings carrying that tag are
        returned, read from the by-tag projection (migration 096) instead of
        the recency feed; an unknown tag is simply an empty partition. The
        same term-expiry filter applies after the LIMITed read either way.
        Raises invalid_request for a blank or over-long tag.

        With `category` (raw; validate_category), only listings declared in
        that category are returned -- the same projection, read at the
        reserved `category:<name>` partition (migration 099). `tag` and
        `category` together are refused (invalid_request): each is one
        partition, and intersecting two would mean reading one and filtering
        the other in memory, which turns the LIMIT into a lie.

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
        if tag is not None and category is not None:
            raise DirectoryError(
                "invalid_request", "tag and category cannot be combined in one search"
            )
        if tag is not None:
            items = self.store.list_by_tag(search_tag(tag), limit=clamped)
        elif category is not None:
            items = self.store.list_by_tag(category_tag(validate_category(category)), limit=clamped)
        else:
            items = self.store.list_recent(limit=clamped)
        return [item for item in items if item.term_end_epoch > cutoff]

    def renew(
        self,
        *,
        normalized_url: str,
        payer: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredListing:
        """Extend a listing's term by one more configured term and return it.

        Only the wallet that paid for the listing's current term may renew
        it, whether or not that term has ended. A renewal keeps everything
        the listing says about the endpoint (price, description, assets,
        tags, schema, category) exactly as it was, so letting a different
        wallet renew an expired listing would put the previous payer's
        description of the endpoint under the new wallet's name. A live
        listing renewed by another wallet is refused with
        listing_owned_by_another_payer (403); an expired or unowned (empty
        payer) listing renewed by a different wallet is refused with
        renew_requires_relist (409): the url is free to take, but only via
        POST /list, which makes the new owner state its own price and
        description. Like create(), no `and payer` guard on the new side:
        an unattributable payment cannot prove it is the owner. The payer
        is only known after settlement, so either refusal comes with the
        payment already taken -- the same accepted tradeoff as create() and
        the board's renew, stated in the 402 offer.

        The new term runs from max(now, current term_end): renewing early
        adds a full term on top of what is left, renewing after expiry
        starts a fresh one from now, and neither shortens what was already
        paid for. created_at is NOT re-stamped -- a renewal buys time, not a
        jump back to the front of the newest-first feed (that is what a
        relist buys). settlement_tx_id is replaced so the listing traces to
        the payment that bought its current term; payer and the verified
        badge (097) are unchanged, since the payer is by construction the
        same wallet.

        Raises not_found if the url is not listed; the route checks this
        before the gate, this is the guard for any other caller.
        """
        moment = now or datetime.now(tz=UTC)
        existing = self.store.get(url_hash(normalized_url))
        if existing is None:
            raise DirectoryError("not_found", "No listing for that url")
        cutoff = int(moment.timestamp())
        if existing.payer != payer:
            if existing.payer and existing.term_end_epoch > cutoff:
                raise DirectoryError(
                    "listing_owned_by_another_payer",
                    "Only the wallet that listed this url may renew it. Payment has "
                    "settled but the existing listing was not changed.",
                )
            raise DirectoryError(
                "renew_requires_relist",
                "This listing has expired (or has no owner) and can only be renewed by "
                "the wallet that listed it. POST /api/v1/x402/list to relist the url "
                "under your wallet. Payment has settled but the existing listing was "
                "not changed.",
                http_status=409,
            )
        base = max(cutoff, existing.term_end_epoch)
        renewed = replace(
            existing,
            term_end_epoch=int(
                (
                    datetime.fromtimestamp(base, tz=UTC)
                    + timedelta(days=settings.x402_listing_term_days)
                ).timestamp()
            ),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.upsert(renewed)
        return renewed

    def detail(
        self, normalized_url: str, *, now: datetime | None = None
    ) -> tuple[StoredListing, StoredProbe | None] | None:
        """Free read: one LIVE listing plus its newest probe, or None if unlisted or expired.

        The one place a caller can read everything the directory holds about
        a single endpoint. Unlike probe_status(), an expired listing is None
        here: this is the per-url twin of search(), and search() has stopped
        serving that listing.
        """
        moment = now or datetime.now(tz=UTC)
        found = self.probe_status(normalized_url)
        if found is None or found[0].term_end_epoch <= int(moment.timestamp()):
            return None
        return found

    def delete(self, normalized_url: str) -> bool:
        """Admin-only: remove a listing outright, feed projection included.

        Takes the URL already normalized, same convention create() uses --
        the route owns normalize_url() and its DirectoryError, this just
        hashes and delegates to the store. Returns False if there was nothing
        to delete, so the admin route can tell a real removal from a no-op.
        """
        return self.store.delete(url_hash(normalized_url))

    def probe_status(self, normalized_url: str) -> tuple[StoredListing, StoredProbe | None] | None:
        """Free read: the listing for a URL plus its newest probe, or None if the URL is not listed.

        Only listed URLs are answerable -- the probe beat only ever probes
        directory listings, so an unlisted URL has no probe row by
        construction, and refusing it here keeps the free route from being
        used to enumerate probe rows by hash. A listed URL that has not been
        probed yet returns (listing, None).
        """
        key = url_hash(normalized_url)
        listing = self.store.get(key)
        if listing is None:
            return None
        return listing, self.store.latest_probe(key)

    def probe_history(self, normalized_url: str, *, limit: int) -> list[StoredProbe] | None:
        """Free read: up to `limit` past probe results for a URL, newest first, or None if unlisted.

        Same existence gate as probe_status() and for the same reason: only
        listed URLs are answerable, so an unlisted URL can't be used to
        enumerate x402_probe_results by hash. An empty list (not None) is a
        listed URL the beat has not reached yet -- see probe_status()'s own
        (listing, None) case.
        """
        key = url_hash(normalized_url)
        if self.store.get(key) is None:
            return None
        clamped = max(1, min(limit, settings.x402_probe_history_max_results))
        return self.store.probe_history(key, limit=clamped)

    def probe_leaderboard(
        self, *, limit: int, now: datetime | None = None
    ) -> tuple[list[ProbeLeaderboardEntry], int]:
        """Rank live listings by MEASURED probe reliability -- never by paid opinion or spend.

        Distinct from x402_grading's leaderboards (a credibility-weighted
        AGGREGATE OF AGENT OPINION -- sybil-vulnerable: enough wallets
        grading small amounts can buy a rank) and from x402_board (pure paid
        presence, no ranking claim at all): this ranks purely on what the
        probe fleet has actually observed (roadmap item 7). Nobody can pay to
        appear higher here -- the same reasoning CLAUDE.md section 9 item 20
        already settled for keeping the measurement layer itself free and
        unbuyable, applied to a leaderboard built on top of it.

        Scans up to PROBE_LEADERBOARD_CANDIDATE_LIMIT live (unexpired)
        listings, newest-created first (list_recent, the same recency feed
        search() reads) -- like search(), the expiry filter runs AFTER the
        LIMITed read rather than as a CQL predicate (CLAUDE.md section 4: no
        ALLOW FILTERING on a non-key column), so a call can scan fewer than
        PROBE_LEADERBOARD_CANDIDATE_LIMIT live candidates when the front of
        the feed holds expired listings. For each live candidate, reads up
        to settings.x402_probe_history_max_results of its most recent probes
        -- the SAME bound the free probe/history route already exposes, so
        this leaderboard never draws on data a caller could not already
        reconstruct by hand from free reads; it only aggregates and ranks it.

        A candidate with fewer than PROBE_LEADERBOARD_MIN_SAMPLES probes in
        that window is skipped entirely -- not scored as 0% or excused as
        100%, just left unranked -- so a listing minutes old with one lucky
        probe cannot outrank one with real history. That is the concrete
        abuse this threshold exists to close.

        "Healthy" for one probe means reachable AND served_valid_402: a probe
        that gets a response but not a valid x402 challenge is not something
        a payer could actually transact against, so it should not count
        toward uptime any more than an unreachable one does. uptime_pct is
        the healthy fraction of the sampled probes (0-100); avg_latency_ms
        averages latency_ms over the healthy ones only, and is None when
        there are none -- an honest missing value, never a fabricated 0 that
        would misread as a great latency (the same "empty is not none found"
        spirit CLAUDE.md section 2 invariant 8 states for a tool's own
        result, applied here to a derived average).

        Ranked by uptime_pct descending, ties broken by avg_latency_ms
        ascending (a candidate with no healthy sample -- uptime 0% -- sorts
        last on latency too, via None ranking after every real value), final
        tie broken by last_probed_at_epoch descending (most recently
        reprobed first), so the order is fully deterministic. The ranked
        list is capped to `limit` (the route clamps this against
        settings.x402_directory_probe_leaderboard_max_results before calling
        in).

        Returns (ranked_entries, candidates_scanned): the second number
        counts every LIVE listing scanned, ranked or not, so a caller can
        tell "we measured N listings and none qualified yet" apart from
        "there is nothing listed at all." Never raises -- a listing with too
        few probes is simply excluded, and an empty or all-too-new directory
        returns ([], candidates_scanned) rather than an error. Unlike
        x402_grading's per-tag leaderboard, this ranks the WHOLE directory
        rather than one tag's listings, so there is no narrower free
        existence check to gate the call on the way a specific tag's grade
        count can be pre-checked -- an honestly-reported empty leaderboard is
        a valid, chargeable answer here (the route's own docstring covers
        why this is priced rather than pre-gate-refused).
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        candidates = [
            item
            for item in self.store.list_recent(limit=PROBE_LEADERBOARD_CANDIDATE_LIMIT)
            if item.term_end_epoch > cutoff
        ]
        entries: list[ProbeLeaderboardEntry] = []
        for item in candidates:
            history = self.store.probe_history(
                item.url_hash, limit=settings.x402_probe_history_max_results
            )
            if len(history) < PROBE_LEADERBOARD_MIN_SAMPLES:
                continue
            healthy = [probe for probe in history if probe.reachable and probe.served_valid_402]
            avg_latency_ms = (
                sum(probe.latency_ms for probe in healthy) / len(healthy) if healthy else None
            )
            entries.append(
                ProbeLeaderboardEntry(
                    url=item.url,
                    verified_wallet=item.verified_wallet if item.is_verified else "",
                    sample_count=len(history),
                    uptime_pct=(len(healthy) / len(history)) * 100.0,
                    avg_latency_ms=avg_latency_ms,
                    # Newest-first (store.probe_history's own contract), so
                    # index 0 is the most recent probe in this sample.
                    last_probed_at_epoch=history[0].probed_at_epoch,
                )
            )
        entries.sort(
            key=lambda entry: (
                -entry.uptime_pct,
                entry.avg_latency_ms if entry.avg_latency_ms is not None else float("inf"),
                -entry.last_probed_at_epoch,
            )
        )
        return entries[: max(0, limit)], len(candidates)
