"""The probe sweep: read live listings, probe each, store results, then set or clear the verified badge.

Ordering per listing (CLAUDE.md section 2, store before mark): the probe
history row, then the latest-row projection, then -- only if the result
changes the badge -- the verified_wallet/verified_at update on the listing
and its projections. A failure anywhere in one listing's handling is logged
and the sweep moves on; nothing per-URL aborts the beat.

The badge rule: set when the endpoint's advertised payTo equals the wallet
that paid for the listing (a live listing only); cleared when a listing
that currently carries a badge serves a well-formed offer whose payTo is a
different wallet. An unreachable endpoint or a malformed offer changes
nothing -- a transient outage must not strip a badge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from algorand_shared.x402_statements import DIRECTORY_PARTITION, X402ProbeStmts

from app.core.cassandra import get_cassandra_session
from app.core.config import X402_PROBE_MAX_LISTINGS
from app.modules.x402_probe.probe import Fetcher, ProbeResult, fetch_unpaid, probe_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListingRow:
    """The slice of a directory listing the sweep needs."""

    url_hash: str
    url: str
    created_at: datetime
    tags: tuple[str, ...]
    term_end: datetime
    payer: str
    verified_wallet: str


class ProbeRepository(Protocol):
    """Storage seam for the sweep; the Cassandra implementation is below, tests fake it."""

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:
        """Return listings whose paid term has not ended, newest first, at most `limit`."""
        ...

    def record_result(self, url_hash: str, result: ProbeResult) -> None:
        """Append the probe to history and overwrite the latest row."""
        ...

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        """Write the badge (or clear it with wallet="" / at=None) on the listing and its projections."""
        ...


class CassandraProbeRepository:
    """Cassandra-backed ProbeRepository over the shared X402ProbeStmts."""

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:
        """Read the recency feed (LIMITed) and drop listings whose term has ended."""
        rows = get_cassandra_session().execute(
            X402ProbeStmts.LIST_LIVE_LISTINGS, (DIRECTORY_PARTITION, limit)
        )
        listings: list[ListingRow] = []
        for row in rows:
            term_end = row.term_end
            if term_end is None:
                continue
            if term_end.tzinfo is None:
                term_end = term_end.replace(tzinfo=UTC)
            if term_end <= now:
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
                )
            )
        return listings

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
        """Canonical row first, then projections; every UPDATE is IF EXISTS (no phantom rows)."""
        session = get_cassandra_session()
        session.execute(X402ProbeStmts.SET_VERIFIED_LISTING, (wallet, at, listing.url_hash))
        session.execute(
            X402ProbeStmts.SET_VERIFIED_RECENCY,
            (wallet, at, DIRECTORY_PARTITION, listing.created_at, listing.url_hash),
        )
        for tag in listing.tags:
            session.execute(
                X402ProbeStmts.SET_VERIFIED_BY_TAG,
                (wallet, at, tag, listing.created_at, listing.url_hash),
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


def run_probe_sweep(
    *,
    repo: ProbeRepository | None = None,
    fetch: Fetcher = fetch_unpaid,
    now: datetime | None = None,
    limit: int = X402_PROBE_MAX_LISTINGS,
) -> dict[str, object]:
    """Probe every live listing once; return counts. Never pays, never aborts on one URL."""
    repo = repo or CassandraProbeRepository()
    moment = now or datetime.now(tz=UTC)
    listings = repo.list_live_listings(now=moment, limit=limit)
    probed = reachable = valid_402 = verified = cleared = failed = 0
    for listing in listings:
        try:
            result = probe_url(listing.url, fetch=fetch, now=moment)
            probed += 1
            reachable += int(result.reachable)
            valid_402 += int(result.served_valid_402)
            repo.record_result(listing.url_hash, result)
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
        "failed": failed,
    }
