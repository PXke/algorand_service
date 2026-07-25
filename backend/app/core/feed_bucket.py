"""Month-bucketed feed partitioning + keyset (cursor) pagination helpers.

`articles_feed` is partitioned by month ('YYYY-MM') so no single partition
grows unbounded. Pagination is keyset on the clustering column (published_at
DESC) walking from the newest month back, which works across partitions
without Cassandra OFFSET.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta


def feed_month(dt: datetime) -> str:
    """Return the 'YYYY-MM' feed bucket key containing dt."""
    return dt.strftime("%Y-%m")


def months_back(start: datetime, count: int) -> Iterator[str]:
    """Yield 'YYYY-MM' buckets from start's month backwards."""
    y, m = start.year, start.month
    for _ in range(count):
        yield f"{y:04d}-{m:02d}"
        m -= 1
        if m == 0:
            m, y = 12, y - 1


def cursor_from_ms(cursor_epoch_ms: int | None) -> datetime:
    """Convert an epoch-ms pagination cursor to a UTC datetime, defaulting to just past now."""
    if cursor_epoch_ms:
        return datetime.fromtimestamp(cursor_epoch_ms / 1000.0, tz=UTC)
    return datetime.now(tz=UTC) + timedelta(seconds=2)


def to_ms(dt: datetime) -> int:
    """Convert a datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)
