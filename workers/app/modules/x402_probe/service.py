"""The probe sweep: read live listings, probe each, store results, then set or clear the verified badge and extend survival.

Ordering per listing (CLAUDE.md section 2, store before mark): the probe
history row, then the latest-row projection, then -- only if the result
changes the badge, and only if the result is healthy for term_end --
the verified_wallet/verified_at update and the term_end extension, both on
the listing and its projections. A failure anywhere in one listing's
handling is logged and the sweep moves on; nothing per-URL aborts the beat.

The badge rule: set when the endpoint's advertised payTo equals the wallet
that paid for the listing (a live listing only); cleared when a listing
that currently carries a badge serves a well-formed offer whose payTo is a
different wallet. An unreachable endpoint or a malformed offer changes
nothing -- a transient outage must not strip a badge.

The term_end rule (2026-09-06 pricing-model change, migration 114): a
HEALTHY probe (reachable AND served_valid_402 -- the same definition
backend's ListingService.probe_leaderboard() already uses for "healthy")
pushes term_end forward to `now + settings.X402_LISTING_TERM_DAYS`, never
backward (see extend_term()'s own docstring). An unhealthy probe changes
term_end NOT AT ALL -- the identical "a transient outage must not strip a
badge" principle, applied to survival instead of the badge. This is the
mechanism that makes a listing survive indefinitely while healthy: without
it a listing would still expire on its original term_end, since nothing
else refreshes it once the paid-renewal-to-survive route was repurposed
into a boost (see backend's ListingService.renew()).

Selection ordering (migration 118, same-night adversarial-review finding
#3): the sweep reads x402_probe_queue in LEAST-RECENTLY-PROBED order, not
newest-created order. The old newest-first read over x402_listings_by_recency
(still available as X402ProbeStmts.LIST_LIVE_LISTINGS, but no longer called
from here) permanently starved every listing past the newest
X402_PROBE_MAX_LISTINGS once the directory grew bigger than that, because
nothing ever advanced past that same LIMITed window -- a perfectly healthy
listing outside it would simply never get reprobed again and expire on
schedule. mark_probed() repositions a listing's queue row to `now` after
EVERY probe attempt, healthy or not, so an unhealthy listing cannot camp at
the front of the queue and starve everyone else either -- the queue is a
pure round-robin over whichever listings have gone longest without being
checked, which guarantees every live listing is revisited within one full
pass of ceil(total_live_listings / X402_PROBE_MAX_LISTINGS) sweeps,
regardless of how many listings exist in total. See
CassandraProbeRepository.list_live_listings/.mark_probed and migration 118's
own comment for the table shape and who writes to it.

Expiry judged off the CANONICAL term_end, not the queue's own copy (second
same-night adversarial-review finding, still 2026-09-06): migration 118's
x402_probe_queue.term_end column is written once at seed time and carried
forward unmodified by every mark_probed() reposition -- a frozen
create/relist-time snapshot that extend_term() never touches (it only
writes x402_listings and its recency/by-tag projections). Judging expiry
off that column meant EVERY listing, healthy or not, silently stopped being
reprobed and got pruned from the queue after X402_LISTING_TERM_DAYS from
its creation/last relist, regardless of how many times extend_term() had
since pushed its real term_end forward -- worse than the bug migration 118
itself fixed, since it hit every listing at any scale rather than only
those past a LIMIT window. CassandraProbeRepository.list_live_listings()
now reads the queue for ordering/position only and judges + prunes expiry
off one bounded, concurrent batch read of the canonical x402_listings.term_end
(X402ProbeStmts.GET_LISTING_TERM_END) across every url_hash the queue page
returned, via app.core.cassandra.execute_parallel_with_args -- a single
round-trip fan-out, not a per-row loop. A transient failure on that batch
read fails OPEN per listing (CLAUDE.md invariant 9, extended from Redis to
Cassandra here): the affected listing is left out of this sweep untouched
(no delete, no probe) rather than treated as expired, so it is simply
retried at its existing queue position next sweep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from algorand_shared.x402_statements import (
    DIRECTORY_PARTITION,
    PROBE_QUEUE_NEVER_PROBED,
    X402ProbeStmts,
)
from celery.exceptions import SoftTimeLimitExceeded

from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
from app.core.config import X402_LISTING_TERM_DAYS, X402_PROBE_MAX_LISTINGS
from app.modules.x402_probe.probe import Fetcher, ProbeResult, fetch_unpaid, probe_url

logger = logging.getLogger(__name__)

# The directory projects each listing's category into x402_listings_by_tag
# under this reserved tag (migration 099). Mirrors backend's
# x402_directory.models.domain.CATEGORY_TAG_PREFIX / DEFAULT_CATEGORY /
# category_tag(), which workers cannot import; the badge must land on that
# row too or `?category=` searches keep serving the stale badge.
CATEGORY_TAG_PREFIX = "category:"
DEFAULT_CATEGORY = "other"


@dataclass(frozen=True)
class ListingRow:
    """The slice of a directory listing the sweep needs."""

    url_hash: str
    url: str
    created_at: datetime
    tags: tuple[str, ...]
    # The listing's REAL, current term_end, as read fresh from the canonical
    # x402_listings row at sweep time -- never the queue row's own
    # denormalized term_end column, which is a frozen create/relist-time
    # snapshot extend_term() never touches (see this module's docstring).
    term_end: datetime
    payer: str
    verified_wallet: str
    category: str = DEFAULT_CATEGORY
    # The x402_probe_queue clustering value this row was read at (migration
    # 118) -- the exact last_probed_at mark_probed() must DELETE before it
    # can insert the repositioned row. Defaults to the "never probed" seed
    # for callers (tests, mainly) that build a ListingRow by hand without
    # going through the queue read.
    queue_position: datetime = PROBE_QUEUE_NEVER_PROBED

    def projection_tags(self) -> tuple[str, ...]:
        """Every by-tag partition this listing has a row in: real tags plus the reserved category tag."""
        return (*self.tags, f"{CATEGORY_TAG_PREFIX}{self.category or DEFAULT_CATEGORY}")


class ProbeRepository(Protocol):
    """Storage seam for the sweep; the Cassandra implementation is below, tests fake it."""

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:
        """Return listings whose paid term has not ended, least-recently-probed first, at most `limit`.

        Expiry is judged off each listing's CANONICAL term_end, never the
        queue row's own denormalized copy (see this module's docstring) --
        a repository backing this Protocol must not resurrect that bug by
        trusting a stored term_end it does not refresh on every read.
        """
        ...

    def record_result(self, url_hash: str, result: ProbeResult) -> None:
        """Append the probe to history and overwrite the latest row."""
        ...

    def mark_probed(self, listing: ListingRow, at: datetime) -> None:
        """Reposition `listing` in the fairness queue to `at`, called for every attempted probe regardless of outcome.

        This is what makes the queue round-robin: a listing that was just
        checked (healthy or not) moves to the back, so the next sweep
        naturally reaches whichever listings have gone longest unchecked.
        """
        ...

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        """Write the badge (or clear it with wallet="" / at=None) on the listing and its projections."""
        ...

    def extend_term(self, listing: ListingRow, until: datetime) -> None:
        """Push term_end forward to `until` on the listing and its projections. Never called for an unhealthy probe."""
        ...


class CassandraProbeRepository:
    """Cassandra-backed ProbeRepository over the shared X402ProbeStmts."""

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:
        """Read the fairness queue for ordering only, then judge/prune expiry off a fresh canonical read.

        The queue (LIMITed, least-recently-probed first) supplies WHICH
        url_hashes to consider next and at what position, never term_end --
        that column is a frozen create/relist-time snapshot mark_probed()
        carries forward unmodified, and extend_term() never writes it (see
        this module's docstring for the incident this fixes). Instead, every
        url_hash this page returned gets one bounded, concurrent batch point
        read of the CANONICAL x402_listings.term_end
        (X402ProbeStmts.GET_LISTING_TERM_END) -- a single fan-out round trip,
        not a per-row loop -- and expiry is judged off THAT value.

        An expired listing's queue row is deleted here rather than merely
        skipped: leaving it in place would let it sit at whatever stale
        position it was last probed at, permanently occupying a slot in
        every future LIMITed read (the exact "old junk crowds out real
        work" bug this table exists to fix, in miniature) instead of aging
        out of the sweep for good. A listing deleted outright (admin
        delist) is self-pruned the same way once its canonical term_end
        naturally passes. A listing whose canonical read itself failed
        (Cassandra hiccup, not "not found") is left untouched instead --
        neither pruned nor probed this sweep -- so a transient error can
        never masquerade as expiry (CLAUDE.md invariant 9, fail open).
        """
        session = get_cassandra_session()
        rows = list(session.execute(X402ProbeStmts.LIST_PROBE_QUEUE, (DIRECTORY_PARTITION, limit)))
        term_ends = self._read_canonical_term_ends([row.url_hash for row in rows])
        listings: list[ListingRow] = []
        for row in rows:
            last_probed_at = row.last_probed_at
            if last_probed_at.tzinfo is None:
                last_probed_at = last_probed_at.replace(tzinfo=UTC)
            if row.url_hash not in term_ends:
                # Canonical read failed for this one listing -- see
                # _read_canonical_term_ends. Skip it for this sweep only;
                # its queue row (and position) is untouched.
                continue
            term_end = term_ends[row.url_hash]
            if term_end is not None and term_end.tzinfo is None:
                term_end = term_end.replace(tzinfo=UTC)
            if term_end is None or term_end <= now:
                session.execute(
                    X402ProbeStmts.DELETE_PROBE_QUEUE,
                    (DIRECTORY_PARTITION, last_probed_at, row.url_hash),
                )
                continue
            listings.append(
                ListingRow(
                    url_hash=row.url_hash,
                    url=row.url or "",
                    created_at=row.created_at,
                    tags=tuple(sorted(row.tags or [])),
                    term_end=term_end,
                    payer=getattr(row, "payer", None) or "",
                    verified_wallet=getattr(row, "verified_wallet", None) or "",
                    # Pre-099 rows read back null: DEFAULT_CATEGORY by definition.
                    category=getattr(row, "category", None) or DEFAULT_CATEGORY,
                    queue_position=last_probed_at,
                )
            )
        return listings

    def _read_canonical_term_ends(self, url_hashes: list[str]) -> dict[str, datetime | None]:
        """Bounded, concurrent point-read of x402_listings.term_end for every given url_hash.

        One fan-out round trip via execute_parallel_with_args, not one query
        per listing -- bounded by the same `limit` the queue page itself was
        read with (CLAUDE.md section 4). A url_hash missing from the
        returned dict means its read failed (Cassandra error, not "row not
        found" -- a genuinely deleted listing comes back as None, which the
        caller correctly treats as expired); the caller must leave that
        listing's queue row untouched rather than treat the failure as
        expiry (CLAUDE.md invariant 9, fail open).
        """
        if not url_hashes:
            return {}
        results = execute_parallel_with_args(
            X402ProbeStmts.GET_LISTING_TERM_END,
            [(url_hash,) for url_hash in url_hashes],
            raise_on_error=False,
        )
        term_ends: dict[str, datetime | None] = {}
        for url_hash, (ok, outcome) in zip(url_hashes, results, strict=True):
            if not ok:
                logger.warning(
                    "x402 probe: canonical term_end read failed for url_hash=%s "
                    "(leaving its queue row untouched this sweep)",
                    url_hash,
                    exc_info=outcome,
                )
                continue
            row = outcome.one()
            term_ends[url_hash] = row.term_end if row is not None else None
        return term_ends

    def mark_probed(self, listing: ListingRow, at: datetime) -> None:
        """Delete the row at `listing.queue_position`, insert a fresh one at `at` -- same shape as a relocate-by-clustering-key move.

        The term_end this reinsert carries is `listing.term_end`, which
        list_live_listings() populated from the CANONICAL read moments ago
        in this same sweep -- so it is fresh as of "now", not stale forever.
        But it is still only an informational copy: nothing here (or
        anywhere else) refreshes it again once a later extend_term() moves
        the canonical value further out, so it must never be read back as
        authoritative -- list_live_listings() never does (see its own
        docstring); this column exists only for operators inspecting the
        table directly, not for any code path's expiry decision.
        """
        session = get_cassandra_session()
        session.execute(
            X402ProbeStmts.DELETE_PROBE_QUEUE,
            (DIRECTORY_PARTITION, listing.queue_position, listing.url_hash),
        )
        session.execute(
            X402ProbeStmts.INSERT_PROBE_QUEUE,
            (
                DIRECTORY_PARTITION,
                at,
                listing.url_hash,
                listing.url,
                listing.created_at,
                set(listing.tags),
                listing.term_end,
                listing.payer,
                listing.verified_wallet,
                listing.category,
            ),
        )

    def record_result(self, url_hash: str, result: ProbeResult) -> None:
        """History row first, then the latest projection (full INSERTs)."""
        params = (
            url_hash,
            result.probed_at,
            result.url,
            result.reachable,
            result.http_status,
            result.latency_ms,
            result.served_valid_402,
            result.payto_seen,
            result.error,
        )
        session = get_cassandra_session()
        session.execute(X402ProbeStmts.INSERT_RESULT, params)
        session.execute(X402ProbeStmts.INSERT_LATEST, params)

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        """Canonical row first, then projections (recency, real tags, category row); every UPDATE is IF EXISTS (no phantom rows)."""
        session = get_cassandra_session()
        session.execute(X402ProbeStmts.SET_VERIFIED_LISTING, (wallet, at, listing.url_hash))
        session.execute(
            X402ProbeStmts.SET_VERIFIED_RECENCY,
            (wallet, at, DIRECTORY_PARTITION, listing.created_at, listing.url_hash),
        )
        for tag in listing.projection_tags():
            session.execute(
                X402ProbeStmts.SET_VERIFIED_BY_TAG,
                (wallet, at, tag, listing.created_at, listing.url_hash),
            )

    def extend_term(self, listing: ListingRow, until: datetime) -> None:
        """Canonical row first, then projections (recency, real tags, category row); every UPDATE is IF EXISTS (no phantom rows). Same shape as set_verified()."""
        session = get_cassandra_session()
        session.execute(X402ProbeStmts.SET_TERM_END_LISTING, (until, listing.url_hash))
        session.execute(
            X402ProbeStmts.SET_TERM_END_RECENCY,
            (until, DIRECTORY_PARTITION, listing.created_at, listing.url_hash),
        )
        for tag in listing.projection_tags():
            session.execute(
                X402ProbeStmts.SET_TERM_END_BY_TAG,
                (until, tag, listing.created_at, listing.url_hash),
            )


def badge_decision(listing: ListingRow, result: ProbeResult) -> str | None:
    """Return the verified_wallet the listing should now carry, or None for "leave as is".

    "" means clear the badge. Only a well-formed 402 offer can move the
    badge in either direction.
    """
    if not result.served_valid_402:
        return None
    payto = result.payto_seen
    if payto and listing.payer and payto == listing.payer:
        return None if listing.verified_wallet == payto else payto
    if listing.verified_wallet and payto != listing.verified_wallet:
        return ""
    return None


def is_healthy(result: ProbeResult) -> bool:
    """A probe counts toward keep-alive (and toward probe_leaderboard()'s own uptime_pct) only when BOTH reachable and served_valid_402 -- a response that isn't a valid x402 challenge is not something a payer could actually transact against, so it must not extend survival any more than an unreachable one does."""
    return result.reachable and result.served_valid_402


def run_probe_sweep(
    *,
    repo: ProbeRepository | None = None,
    fetch: Fetcher = fetch_unpaid,
    now: datetime | None = None,
    limit: int = X402_PROBE_MAX_LISTINGS,
) -> dict[str, object]:
    """Probe every live listing once; return counts. Never pays, never aborts on one URL.

    The one exception to per-URL isolation is Celery's SoftTimeLimitExceeded
    (a plain Exception subclass): it is re-raised immediately so the task
    ends at the soft limit instead of sweeping on until the hard kill
    (CLAUDE.md invariant 6). The single_flight lock on the task releases in
    its own `finally`, so the re-raise leaves no lock behind.
    """
    repo = repo or CassandraProbeRepository()
    moment = now or datetime.now(tz=UTC)
    listings = repo.list_live_listings(now=moment, limit=limit)
    probed = reachable = valid_402 = verified = cleared = extended = failed = 0
    for listing in listings:
        try:
            result = probe_url(listing.url, fetch=fetch, now=moment)
            probed += 1
            reachable += int(result.reachable)
            valid_402 += int(result.served_valid_402)
            # Store before mark (CLAUDE.md section 2): the history/latest
            # rows land before anything derived from this result (badge,
            # term_end) is written.
            repo.record_result(listing.url_hash, result)
            # Reposition in the fairness queue for EVERY attempted probe,
            # healthy or not (migration 118, finding #3): this is what makes
            # the queue round-robin instead of letting an unhealthy listing
            # camp at the front and starve the rest of the sweep.
            repo.mark_probed(listing, moment)

            if is_healthy(result):
                # Never backward: a listing whose term_end was already
                # pushed further out (e.g. by a later probe processed
                # earlier in a re-run) must not have that erased by an
                # older-clocked healthy result.
                target = max(listing.term_end, moment + timedelta(days=X402_LISTING_TERM_DAYS))
                if target > listing.term_end:
                    repo.extend_term(listing, target)
                    extended += 1

            decision = badge_decision(listing, result)
            if decision is None:
                continue
            if decision:
                repo.set_verified(listing, decision, moment)
                verified += 1
                logger.info("x402 probe verified url=%s wallet=%s", listing.url, decision)
            else:
                repo.set_verified(listing, "", None)
                cleared += 1
                logger.warning(
                    "x402 probe cleared badge url=%s was=%s now_advertises=%s",
                    listing.url,
                    listing.verified_wallet,
                    result.payto_seen,
                )
        except SoftTimeLimitExceeded:
            logger.warning(
                "x402 probe sweep hit the soft time limit after %d/%d listings",
                probed,
                len(listings),
            )
            raise
        except Exception:
            failed += 1
            logger.warning(
                "x402 probe failed for url=%s (continuing sweep)", listing.url, exc_info=True
            )
    return {
        "status": "ok",
        "listings": len(listings),
        "probed": probed,
        "reachable": reachable,
        "valid_402": valid_402,
        "verified": verified,
        "cleared": cleared,
        "extended": extended,
        "failed": failed,
    }
