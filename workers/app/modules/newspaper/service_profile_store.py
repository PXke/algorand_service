from __future__ import annotations

from datetime import UTC, datetime

from app.modules.newspaper.service_profile import score_service_impressiveness


def get_stored_service_weight(service_id: str) -> int:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    row = session.execute(
        """
        SELECT impressiveness_score FROM service_profiles
        WHERE service_id = %s
        """,
        (service_id,),
    ).one()
    if row is None or row.impressiveness_score is None:
        return 0
    return int(row.impressiveness_score)


def upsert_service_profile(
    *,
    service_id: str,
    page_text: str,
    source_url: str = "",
) -> int:
    score, reasons = score_service_impressiveness(
        page_text=page_text,
        source_url=source_url,
    )
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO service_profiles (
          service_id, impressiveness_score, text_chars, reasons, updated_at
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        (
            service_id,
            score,
            len(page_text.strip()),
            reasons,
            datetime.now(tz=UTC),
        ),
    )
    return score
