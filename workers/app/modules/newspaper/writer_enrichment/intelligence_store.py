"""Cassandra storage for a service's accumulated writer intelligence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def load_intelligence(service_id: str) -> dict[str, Any] | None:
    """Load a service's stored writer-intelligence bundle, or None if none exists."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import IntelligenceStmts

    session = get_cassandra_session()
    row = session.execute(IntelligenceStmts.GET, (service_id,)).one()
    if row is None or not row.intelligence_json:
        return None
    try:
        data = json.loads(row.intelligence_json)
        if isinstance(data, dict):
            data.setdefault("primary_domain", row.primary_domain or "")
            return data
    except Exception:
        return None
    return None


def save_intelligence(
    *,
    service_id: str,
    primary_domain: str,
    bundle_dict: dict[str, Any],
    is_first: bool,
) -> None:
    """Persist a service's writer-intelligence bundle, preserving first-seen time."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import IntelligenceStmts

    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    existing = session.execute(IntelligenceStmts.GET_FIRST_SEEN, (service_id,)).one()
    first_seen = existing.first_seen_at if existing and existing.first_seen_at else now
    if is_first:
        first_seen = now

    domains = bundle_dict.get("sections", {}).get("domains", {}).get("listed", [])
    payload = {
        "primary_domain": primary_domain,
        "domains": domains if isinstance(domains, list) else [],
        "phase": bundle_dict.get("phase"),
        "saved_at": now.isoformat(),
    }

    session.execute(
        IntelligenceStmts.INSERT,
        (
            service_id,
            primary_domain,
            json.dumps(payload, separators=(",", ":")),
            first_seen,
            now,
        ),
    )
