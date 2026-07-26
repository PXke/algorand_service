"""Service-registry storage interface, in-memory and Cassandra implementations."""

from __future__ import annotations

from typing import Any, Protocol

from app.modules.registry.models import ServiceEntry


class ServiceRegistryRepository(Protocol):
    """Storage interface for the service registry."""

    def list_enabled(self) -> list[ServiceEntry]:
        """List enabled service-registry entries."""
        ...

    def list_all(self) -> list[ServiceEntry]:
        """List every service-registry entry, enabled or not."""
        ...

    def upsert(self, entry: ServiceEntry) -> None:
        """Insert or update a service-registry entry."""
        ...

    def delete(self, service_id: str) -> None:
        """Delete a service-registry entry by id."""
        ...


class InMemoryServiceRegistryRepository:
    """In-memory service registry for tests."""

    def __init__(self, entries: list[ServiceEntry] | None = None) -> None:
        """Seed the in-process registry with the given entries, or none."""
        self._entries = list(entries or [])

    def list_enabled(self) -> list[ServiceEntry]:
        """List enabled service-registry entries."""
        return [entry for entry in self._entries if entry.enabled]

    def list_all(self) -> list[ServiceEntry]:
        """List every service-registry entry, enabled or not."""
        return list(self._entries)

    def upsert(self, entry: ServiceEntry) -> None:
        """Insert or update a service-registry entry."""
        self._entries = [e for e in self._entries if e.service_id != entry.service_id]
        self._entries.append(entry)

    def delete(self, service_id: str) -> None:
        """Delete a service-registry entry by id."""
        self._entries = [e for e in self._entries if e.service_id != service_id]


class CassandraServiceRegistryRepository:
    """Cassandra-backed service registry."""

    def list_enabled(self) -> list[ServiceEntry]:
        """List enabled service-registry entries."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ServiceRegistryStmts

        session = get_cassandra_session()
        rows = session.execute(ServiceRegistryStmts.LIST_ALL)
        return [_row_to_entry(row) for row in rows if row.enabled]

    def list_all(self) -> list[ServiceEntry]:
        """List every service-registry entry, enabled or not."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ServiceRegistryStmts

        session = get_cassandra_session()
        rows = session.execute(ServiceRegistryStmts.LIST_ALL)
        return [_row_to_entry(row) for row in rows]

    def upsert(self, entry: ServiceEntry) -> None:
        """Insert or update a service-registry entry."""
        from datetime import UTC, datetime

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ServiceRegistryStmts

        session = get_cassandra_session()
        session.execute(
            ServiceRegistryStmts.UPSERT,
            (
                entry.service_id,
                entry.display_name,
                entry.match_kind,
                entry.match_value,
                entry.scrape_url,
                entry.enabled,
                datetime.now(tz=UTC),
                entry.origin or "seed",
            ),
        )

    def delete(self, service_id: str) -> None:
        """Delete a service-registry entry by id."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ServiceRegistryStmts

        session = get_cassandra_session()
        session.execute(ServiceRegistryStmts.DELETE, (service_id,))


def _effective_origin(row: Any, *, match_kind: str) -> str:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
    explicit = (getattr(row, "origin", None) or "").strip()
    if explicit:
        return explicit
    if match_kind == "domain":
        return "domain"
    return "seed"


def _row_to_entry(row: Any) -> ServiceEntry:  # noqa: ANN401 -- duck-typed Cassandra driver row, no formal class
    service_id = str(row.service_id or "").strip()
    scrape_url = getattr(row, "scrape_url", None)
    raw_match_kind = (row.match_kind or "").strip()
    origin = _effective_origin(row, match_kind=raw_match_kind)
    # Domain-promoted rows can exist with only service_id/scrape_url set; coerce
    # nulls so list endpoints do not fail Pydantic validation.
    display_name = (row.display_name or service_id or "").strip()
    match_kind = (raw_match_kind or ("domain" if origin == "domain" else "")).strip()
    match_value = (row.match_value or scrape_url or service_id or "").strip()
    return ServiceEntry(
        service_id=service_id,
        display_name=display_name,
        match_kind=match_kind,
        match_value=match_value,
        scrape_url=scrape_url,
        origin=origin,
        enabled=bool(row.enabled),
    )


_registry: ServiceRegistryRepository | None = None


def get_service_registry_repository() -> ServiceRegistryRepository:
    """Return the process-wide registry repository, creating it lazily."""
    global _registry
    if _registry is None:
        _registry = CassandraServiceRegistryRepository()
    return _registry


def set_service_registry_repository(repository: ServiceRegistryRepository | None) -> None:
    """Override the process-wide registry repository (used by tests)."""
    global _registry
    _registry = repository
