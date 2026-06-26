from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ServiceEntry:
    service_id: str
    display_name: str
    match_kind: str
    match_value: str
    scrape_url: str | None
    enabled: bool = True


@lru_cache(maxsize=1)
def load_enabled_services() -> tuple[ServiceEntry, ...]:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = session.execute(
        """
        SELECT service_id, display_name, match_kind, match_value, scrape_url, enabled
        FROM service_registry
        """
    )
    entries: list[ServiceEntry] = []
    for row in rows:
        if not row.enabled:
            continue
        entries.append(
            ServiceEntry(
                service_id=row.service_id,
                display_name=row.display_name,
                match_kind=row.match_kind,
                match_value=row.match_value,
                scrape_url=getattr(row, "scrape_url", None),
            )
        )
    return tuple(entries)


def clear_registry_cache() -> None:
    load_enabled_services.cache_clear()
