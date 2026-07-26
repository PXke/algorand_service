"""Month-bucketed feed partitioning + keyset (cursor) pagination helpers.

`articles_feed` is partitioned by month ('YYYY-MM') so no single partition
grows unbounded. Pagination is keyset on the clustering column (published_at
DESC) walking from the newest month back, which works across partitions
without Cassandra OFFSET.

Both deployables write and read that projection, so the bucket rule has to be
identical on both sides — a divergence here writes rows into a partition the
reader never scans. The workers copy was previously a hand-maintained "mirror
of the backend helper".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def feed_month(dt: datetime) -> str:
    """Return the 'YYYY-MM' feed bucket key containing dt."""
    return f"{dt.year:04d}-{dt.month:02d}"


def months_back(start: datetime, count: int) -> list[str]:
    """Yield 'YYYY-MM' buckets from start's month backwards."""
    out: list[str] = []
    year, month = start.year, start.month
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return out


def cursor_from_ms(cursor_epoch_ms: int | None) -> datetime:
    """Convert an epoch-ms pagination cursor to a UTC datetime, defaulting to just past now."""
    if cursor_epoch_ms:
        return datetime.fromtimestamp(cursor_epoch_ms / 1000.0, tz=UTC)
    return datetime.now(tz=UTC) + timedelta(seconds=2)


def to_ms(dt: datetime) -> int:
    """Convert a datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)
