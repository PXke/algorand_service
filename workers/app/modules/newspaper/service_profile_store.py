"""Cassandra storage for a service's computed profile weight."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.newspaper.service_profile import score_service_impressiveness


def get_stored_service_weight(service_id: str) -> int:
    """Return a service's stored impressiveness score, or 0 if never scored."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceProfileStmts

    session = get_cassandra_session()
    row = session.execute(ServiceProfileStmts.GET_WEIGHT, (service_id,)).one()
    if row is None or row.impressiveness_score is None:
        return 0
    return int(row.impressiveness_score)


def upsert_service_profile(
    *,
    service_id: str,
    page_text: str,
    source_url: str = "",
) -> int:
    """Score a service's page text, persist the profile, and return the score."""
    score, reasons = score_service_impressiveness(
        page_text=page_text,
        source_url=source_url,
    )
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceProfileStmts

    session = get_cassandra_session()
    session.execute(
        ServiceProfileStmts.INSERT,
        (
            service_id,
            score,
            len(page_text.strip()),
            reasons,
            datetime.now(tz=UTC),
        ),
    )
    return score
