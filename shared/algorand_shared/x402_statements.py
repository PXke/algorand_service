"""Prepared CQL shared by the x402 probe beat (workers) and the directory read side (backend).

The x402 directory's own listing statements stay in backend's
`app/core/statements.py` (`X402DirectoryStmts`): only backend writes
listings. This module holds the statements BOTH services touch since the
probe/monitoring lane (migration 097): workers reads the live listings and
writes probe results + the verified badge, backend reads the badge (via its
own SELECTs) and the latest probe row.

Same `_Stmt` descriptor shape as `artifact_statements.py`: preparation is
delegated to whichever service's `app.core.cassandra.prepare_cached` is
importing this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement

# Constant partition key of x402_listings_by_recency (migration 090). Mirrors
# backend's x402_directory.models.domain.DIRECTORY_PARTITION, which cannot be
# imported from here (shared code never imports `app.*`).
DIRECTORY_PARTITION = "default"

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

    # The probe sweep's input: the newest-first recency feed, LIMITed and
    # bound (CLAUDE.md section 4). Term expiry is filtered by the caller.
    LIST_LIVE_LISTINGS = _Stmt(
        "SELECT url_hash, url, created_at, tags, term_end, payer, "
        "verified_wallet, verified_at "
        "FROM algorand_platform.x402_listings_by_recency "
        "WHERE directory = ? LIMIT ?"
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
