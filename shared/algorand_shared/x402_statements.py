"""Prepared CQL shared by the x402 probe beat (workers) and the directory read side (backend).

The x402 directory's own listing statements stay in backend's
`app/core/statements.py` (`X402DirectoryStmts`): only backend writes
listings. This module holds the statements BOTH services touch since the
probe/monitoring lane (migration 097): workers reads the live listings and
writes probe results + the verified badge, backend reads the badge (via its
own SELECTs) and the latest probe row. Since the 2026-09-06 pricing-model
change (migration 114), workers also writes term_end itself on every
healthy probe -- see the SET_TERM_END_* statements and
workers/app/modules/x402_probe/service.py's run_probe_sweep().

x402_probe_queue (migration 118, same-night adversarial-review finding #3):
the sweep's read source, ordered by LEAST-RECENTLY-PROBED rather than
newest-created, so a directory bigger than X402_PROBE_MAX_LISTINGS rows
cannot permanently strand its oldest listings outside the sweep's LIMITed
read window -- see that migration's own comment for the full incident and
workers/app/modules/x402_probe/service.py's module docstring for the
mechanics. BOTH services write to it: backend seeds a listing into it (at
the PROBE_QUEUE_NEVER_PROBED sentinel) on create()/relist(), workers
repositions it after every probe attempt via CassandraProbeRepository
.mark_probed().

x402_probe_queue.term_end is NOT the source of truth for a listing's expiry
(second same-night adversarial-review finding, still 2026-09-06): it is
written once at seed time and carried forward unmodified by every
mark_probed() reposition, so it is a frozen create/relist-time snapshot,
never refreshed by extend_term() (which only ever writes the canonical
x402_listings row and its recency/by-tag projections -- see
SET_TERM_END_LISTING/RECENCY/BY_TAG below). The sweep therefore never
trusts that column: CassandraProbeRepository.list_live_listings() reads it
only for ordering/position, then does one bounded, concurrent batch read of
GET_LISTING_TERM_END (below) across every url_hash the queue page returned,
and judges + prunes expiry off THAT canonical value. See
workers/app/modules/x402_probe/service.py's module docstring for the full
incident this fixes.

Same `_Stmt` descriptor shape as `artifact_statements.py`: preparation is
delegated to whichever service's `app.core.cassandra.prepare_cached` is
importing this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement

# Constant partition key of x402_listings_by_recency (migration 090). Mirrors
# backend's x402_directory.models.domain.DIRECTORY_PARTITION, which cannot be
# imported from here (shared code never imports `app.*`).
DIRECTORY_PARTITION = "default"

# Seed value backend writes into x402_probe_queue.last_probed_at for a
# listing that has never been probed (or was just relisted): the smallest
# representable timestamp, so it sorts to the front of the table's ASC
# clustering order and gets picked up on the sweep's very next run. Writing
# the SAME constant on every seed (rather than e.g. `now`) is what makes the
# seed insert idempotent -- relisting the same url twice before it is ever
# probed overwrites the same row instead of creating a duplicate.
PROBE_QUEUE_NEVER_PROBED = datetime(1970, 1, 1, tzinfo=UTC)

_PROBE_COLUMNS = (
    "url_hash, probed_at, url, reachable, http_status, latency_ms, "
    "served_valid_402, payto_seen, error"
)


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access."""

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


class X402ProbeStmts:
    """Prepared statements for x402_probe_results / x402_probe_latest and the verified badge."""

    # SUPERSEDED as the sweep's read source by LIST_PROBE_QUEUE below
    # (migration 118, finding #3): this newest-first read permanently
    # starved any listing past the newest X402_PROBE_MAX_LISTINGS once the
    # directory grew past that many rows, since nothing ever pruned an
    # expired row from this feed. Kept only for
    # workers/scratch/backfill_x402_probe_queue.py, which pages through the
    # WHOLE feed once (no LIMIT-window problem for a one-off, non-recurring
    # full scan) to seed x402_probe_queue for every listing that predates
    # migration 118. Do not reintroduce this as a live sweep read path.
    LIST_LIVE_LISTINGS = _Stmt(
        "SELECT url_hash, url, created_at, tags, term_end, payer, "
        "verified_wallet, verified_at, category "
        "FROM algorand_platform.x402_listings_by_recency "
        "WHERE directory = ? LIMIT ?"
    )
    # The sweep's real input (migration 118): the fairness queue, read in its
    # native ASC clustering order (least-recently-probed first) -- no ORDER
    # BY needed, same "clustering order needs none" shape as LIST_HISTORY
    # below. Term expiry is filtered (and the stale row pruned) by the
    # caller -- see CassandraProbeRepository.list_live_listings.
    LIST_PROBE_QUEUE = _Stmt(
        "SELECT url_hash, url, created_at, tags, term_end, payer, "
        "verified_wallet, category, last_probed_at "
        "FROM algorand_platform.x402_probe_queue "
        "WHERE directory = ? LIMIT ?"
    )
    # Full INSERT (never a partial UPDATE, CLAUDE.md section 3): written by
    # backend at listing create()/relist() time (seeding, always at
    # PROBE_QUEUE_NEVER_PROBED) and by workers after every probe attempt
    # (repositioning, at the probe's own timestamp) -- see
    # CassandraProbeRepository.mark_probed.
    INSERT_PROBE_QUEUE = _Stmt(
        "INSERT INTO algorand_platform.x402_probe_queue ("
        "directory, last_probed_at, url_hash, url, created_at, tags, "
        "term_end, payer, verified_wallet, category"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # Deletes the row a reposition (or an expired-on-read prune) supersedes.
    # last_probed_at is a clustering column, so the row's exact current
    # position is needed -- the caller always has it, either from the
    # PROBE_QUEUE_NEVER_PROBED seed value or from the row it just read via
    # LIST_PROBE_QUEUE.
    DELETE_PROBE_QUEUE = _Stmt(
        "DELETE FROM algorand_platform.x402_probe_queue "
        "WHERE directory = ? AND last_probed_at = ? AND url_hash = ?"
    )
    # Full INSERTs, never partial UPDATEs (phantom-row class, section 3).
    INSERT_RESULT = _Stmt(
        f"INSERT INTO algorand_platform.x402_probe_results ({_PROBE_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_LATEST = _Stmt(
        f"INSERT INTO algorand_platform.x402_probe_latest ({_PROBE_COLUMNS}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    GET_LATEST = _Stmt(
        f"SELECT {_PROBE_COLUMNS} FROM algorand_platform.x402_probe_latest WHERE url_hash = ?"
    )
    # Point read of the CANONICAL term_end (the same column extend_term()
    # keeps current via SET_TERM_END_LISTING) -- the sweep's real source of
    # truth for expiry, batched concurrently over every url_hash a
    # LIST_PROBE_QUEUE page returned via app.core.cassandra
    # .execute_parallel_with_args rather than one query per listing. See the
    # module docstring above for why the queue row's own term_end column is
    # never used for this.
    GET_LISTING_TERM_END = _Stmt(
        "SELECT term_end FROM algorand_platform.x402_listings WHERE url_hash = ?"
    )
    # Bounded (CLAUDE.md section 4) -- the caller clamps limit; newest first
    # is x402_probe_results' own clustering order (097), no ORDER BY needed.
    LIST_HISTORY = _Stmt(
        f"SELECT {_PROBE_COLUMNS} FROM algorand_platform.x402_probe_results "
        "WHERE url_hash = ? LIMIT ?"
    )
    # Badge writes are the ONE partial UPDATE on the listing tables, made
    # safe with IF EXISTS: a listing deleted (admin delist) between the
    # sweep's read and this write is left deleted instead of resurrected as
    # a row whose every other column is null. The projections need the
    # exact clustering key (created_at) read alongside the listing.
    SET_VERIFIED_LISTING = _Stmt(
        "UPDATE algorand_platform.x402_listings "
        "SET verified_wallet = ?, verified_at = ? WHERE url_hash = ? IF EXISTS"
    )
    SET_VERIFIED_RECENCY = _Stmt(
        "UPDATE algorand_platform.x402_listings_by_recency "
        "SET verified_wallet = ?, verified_at = ? "
        "WHERE directory = ? AND created_at = ? AND url_hash = ? IF EXISTS"
    )
    SET_VERIFIED_BY_TAG = _Stmt(
        "UPDATE algorand_platform.x402_listings_by_tag "
        "SET verified_wallet = ?, verified_at = ? "
        "WHERE tag = ? AND created_at = ? AND url_hash = ? IF EXISTS"
    )
    # Probe-fed keep-alive (migration 114's pricing-model change, backend's
    # settings.x402_listing_term_days): a HEALTHY probe (reachable AND
    # served_valid_402, the same definition backend's probe_leaderboard()
    # uses) pushes term_end forward -- an unhealthy one must never touch it,
    # same "a transient outage must not strip a badge" principle as the
    # verified-badge writes above, applied to survival instead of the badge.
    # Same partial-UPDATE-with-IF-EXISTS shape as SET_VERIFIED_* for the same
    # reason: a listing deleted (admin delist) between the sweep's read and
    # this write is left deleted instead of resurrected as a row whose every
    # other column is null.
    SET_TERM_END_LISTING = _Stmt(
        "UPDATE algorand_platform.x402_listings SET term_end = ? WHERE url_hash = ? IF EXISTS"
    )
    SET_TERM_END_RECENCY = _Stmt(
        "UPDATE algorand_platform.x402_listings_by_recency SET term_end = ? "
        "WHERE directory = ? AND created_at = ? AND url_hash = ? IF EXISTS"
    )
    SET_TERM_END_BY_TAG = _Stmt(
        "UPDATE algorand_platform.x402_listings_by_tag SET term_end = ? "
        "WHERE tag = ? AND created_at = ? AND url_hash = ? IF EXISTS"
    )
