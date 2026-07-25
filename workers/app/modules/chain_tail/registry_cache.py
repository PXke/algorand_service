"""Cached view of enabled services for the chain-tail matcher."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ServiceEntry:
    """One enabled service's chain-matching config."""
    service_id: str
    display_name: str
    match_kind: str
    match_value: str
    scrape_url: str | None
    enabled: bool = True


@lru_cache(maxsize=1)
def load_enabled_services() -> tuple[ServiceEntry, ...]:
    """Load and cache the enabled service registry rows for chain-tail matching."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts

    session = get_cassandra_session()
    rows = session.execute(ServiceRegistryStmts.LIST_ALL)
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
    """Evict the cached enabled-services registry so the next load re-queries Cassandra."""
    load_enabled_services.cache_clear()
