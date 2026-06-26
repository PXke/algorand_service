"""Month-bucketed feed partitioning helpers (mirror of the backend helper)."""

from __future__ import annotations

from datetime import datetime


def feed_month(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def months_back(start: datetime, count: int):
    y, m = start.year, start.month
    for _ in range(count):
        yield f"{y:04d}-{m:02d}"
        m -= 1
        if m == 0:
            m, y = 12, y - 1
