"""One-off backfill for migration 118 (x402_probe_queue).

The probe sweep's read source moved from x402_listings_by_recency (newest-
created-first, LIMITed, never pruned of expired rows -- the bug this
migration exists to fix, see that migration's own comment and
workers/app/modules/x402_probe/service.py's module docstring) to
x402_probe_queue (least-recently-probed-first). Going forward, every PAID
listing is seeded into the new table automatically at create()/relist() time
(backend's CassandraListingStore._write_projections). Every listing that
predates migration 118 has zero rows there until this script runs once.

Read-only against x402_listings_by_recency, additive writes to
x402_probe_queue. Safe to re-run: the seed is a full INSERT at the constant
PROBE_QUEUE_NEVER_PROBED clustering key, so running this twice (or running it
after some listings have already been organically probed) just re-seeds
those listings for an extra, harmless early reprobe rather than creating
duplicate rows -- same idempotence argument as the live seed write's own
docstring.

Only PAID listings are seeded (x402_listings_by_recency is already a PAID-
ONLY feed, migration 113) -- an auto-discovered stub has never been covered
by the probe sweep and this script does not change that.

Usage (after migration 118 has been applied to the target keyspace):
    PYTHONPATH=.:../shared python workers/scratch/backfill_x402_probe_queue.py

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

from datetime import UTC, datetime

from algorand_shared.x402_statements import (
    DIRECTORY_PARTITION,
    PROBE_QUEUE_NEVER_PROBED,
    X402ProbeStmts,
)

from app.core.cassandra import get_cassandra_session

# One page, covering comfortably more than this directory will hold for the
# life of the contest -- a one-off backfill script, not a recurring bounded
# read, so this is exempt from the live sweep's per-run LIMIT discipline
# (CLAUDE.md section 4 governs hot-path reads; this runs once by hand).
_BACKFILL_LIMIT = 1_000_000


def main() -> None:
    """Seed x402_probe_queue from every currently-live row in x402_listings_by_recency."""
    session = get_cassandra_session()
    session.default_fetch_size = 2000

    scanned = 0
    seeded = 0
    skipped_expired = 0
    now = datetime.now(tz=UTC)

    rows = session.execute(
        X402ProbeStmts.LIST_LIVE_LISTINGS, (DIRECTORY_PARTITION, _BACKFILL_LIMIT)
    )
    for row in rows:
        scanned += 1
        term_end = row.term_end
        if term_end is None:
            skipped_expired += 1
            continue
        if term_end.tzinfo is None:
            term_end = term_end.replace(tzinfo=UTC)
        if term_end <= now:
            skipped_expired += 1
            continue
        session.execute(
            X402ProbeStmts.INSERT_PROBE_QUEUE,
            (
                DIRECTORY_PARTITION,
                PROBE_QUEUE_NEVER_PROBED,
                row.url_hash,
                row.url or "",
                row.created_at,
                set(row.tags or []),
                term_end,
                getattr(row, "payer", None) or "",
                getattr(row, "verified_wallet", None) or "",
                getattr(row, "category", None) or "other",
            ),
        )
        seeded += 1

    print(  # noqa: T201 -- one-off operator script, not library code
        f"x402_probe_queue backfill: scanned={scanned} seeded={seeded} "
        f"skipped_expired={skipped_expired}"
    )


if __name__ == "__main__":
    main()
