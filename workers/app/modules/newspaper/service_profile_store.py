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


def get_stored_scale_signal(service_id: str) -> tuple[float | None, datetime | None]:
    """Return (scale_score, scale_updated_at), or (None, None) if never scored."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceProfileStmts

    session = get_cassandra_session()
    row = session.execute(ServiceProfileStmts.GET_SCALE, (service_id,)).one()
    if row is None or row.scale_score is None:
        return None, None
    return float(row.scale_score), row.scale_updated_at


def upsert_service_scale(*, service_id: str, scale_score: float, scale_source: str) -> None:
    """Persist a freshly-resolved scale signal for one service."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceProfileStmts

    session = get_cassandra_session()
    session.execute(
        ServiceProfileStmts.UPSERT_SCALE,
        (scale_score, scale_source, datetime.now(tz=UTC), service_id),
    )


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
