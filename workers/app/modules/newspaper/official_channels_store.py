from __future__ import annotations

import time

_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, set[str]]] = {}


def load_official_channel_ids(kind: str) -> set[str]:
    """
    Official channel/domain allowlist from Cassandra (admin-managed),
    cached briefly. Returns empty set when the table is unavailable so
    env-based allowlists keep working alone.
    """
    now = time.monotonic()
    cached = _cache.get(kind)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    values: set[str] = set()
    try:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        rows = session.execute(
            "SELECT channel_id FROM official_channels WHERE kind = %s",
            (kind,),
        )
        values = {str(row.channel_id).strip().lower() for row in rows if row.channel_id}
    except Exception:
        return cached[1] if cached else set()

    _cache[kind] = (now, values)
    return values


def clear_official_channels_cache() -> None:
    _cache.clear()
