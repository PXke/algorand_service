from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ServiceSource:
    source_id: str
    source_type: str  # web | youtube | mail | bluesky
    url: str
    domain: str  # registrable domain (eTLD+1) for web sources, "" otherwise
    enabled: bool = True


def add_web_source(service_id: str, *, domain: str, url: str) -> None:
    """Attach a web source to a service and claim the domain's reverse lookup.
    Idempotent (PK upserts). source_id keys on the URL's HOST — one entity's
    subdomains (stake./app./docs.folks.finance) are distinct sources of ONE
    service, each contributing its pages to the aggregate. The reverse row is
    what lets a URL resolve to its OWNING service, so a merged domain
    (algorand.com → the algorand-co service) stops spawning its own service."""
    from urllib.parse import urlparse

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id or not domain:
        return
    resolved_url = url or f"https://{domain}"
    host = (urlparse(resolved_url).netloc or "").lower() or domain
    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    session.execute(
        ServiceSourceStmts.UPSERT,
        (service_id, f"web:{host}", "web", resolved_url, domain, True, now),
    )
    session.execute(ServiceSourceStmts.UPSERT_BY_DOMAIN, (domain, service_id))


def list_sources(service_id: str) -> list[ServiceSource]:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id:
        return []
    rows = get_cassandra_session().execute(
        ServiceSourceStmts.LIST_FOR_SERVICE, (service_id,)
    )
    return [
        ServiceSource(
            source_id=r.source_id,
            source_type=r.source_type or "",
            url=r.url or "",
            domain=r.domain or "",
            enabled=bool(r.enabled) if r.enabled is not None else True,
        )
        for r in rows
    ]


def web_domains_for_service(service_id: str) -> list[str]:
    """Registrable domains of the service's enabled web sources (aggregation
    scope for the weekly service-watch context)."""
    return sorted(
        {
            s.domain
            for s in list_sources(service_id)
            if s.enabled and s.source_type == "web" and s.domain
        }
    )


def service_for_domain(domain: str) -> str:
    """service_id owning this registrable domain, or "" when unclaimed."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not domain:
        return ""
    row = get_cassandra_session().execute(
        ServiceSourceStmts.GET_BY_DOMAIN, (domain,)
    ).one()
    return str(row.service_id) if row and row.service_id else ""
