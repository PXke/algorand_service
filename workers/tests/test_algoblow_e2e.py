from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from cassandra.cluster import Session

from app.core.cassandra import get_cassandra_session
from app.modules.newspaper.article_matching import build_match_keys
from app.modules.newspaper.article_store import get_article
from app.modules.newspaper.article_version_store import list_article_versions as list_versions
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.newspaper.publish_policy import PublishTier
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.snapshot_store import source_id_for_service
from app.modules.newspaper.tasks.publish_tasks import publish_from_queued_row

_DISCORD_SERVICE = "discord:algorand-foundation:announcements"
_X_SERVICE = "x:d13_co"


def _clear_snapshots(session: Session, service_id: str) -> None:
    session.execute(
        "DELETE FROM page_snapshots WHERE source_id = %s",
        [source_id_for_service(service_id)],
    )


def _load_queue_row(session: Session, queue_id: str) -> QueuedPublishRow:
    row = session.execute(
        "SELECT * FROM publish_queue WHERE queue_id = %s",
        (uuid.UUID(hex=queue_id),),
    ).one()
    assert row is not None
    created = row.created_at
    return QueuedPublishRow(
        queue_id=str(row.queue_id),
        priority=int(row.priority),
        topic=row.topic or "",
        publish_kind=row.publish_kind or "",
        service_id=row.service_id or "",
        display_name=row.display_name or "",
        scrape_url=row.scrape_url or "",
        payload=json.loads(row.payload or "{}"),
        created_at_epoch=int(created.timestamp()) if created else 0,
    )


def _clear_match_keys(session: Session) -> None:
    """Remove match keys left by previous runs so v1 resolves as a create."""
    with open("tests/fixtures/algoblow_d13_alert.txt", encoding="utf-8") as f:
        x_text = f.read()
    keys = build_match_keys(
        service_id=_DISCORD_SERVICE,
        page_text="Warning: algoblow.com is a scam.",
        source_url="https://discord.com/channels/algorand-foundation/123456789",
        extra_keywords=("scam", "rekey"),
    ) + build_match_keys(
        service_id=_X_SERVICE,
        page_text=x_text,
        source_url="https://x.com/d13_co/status/2060386210732761317",
        extra_keywords=("scam", "rekey"),
    )
    for key_type, key_value in keys:
        session.execute(
            "DELETE FROM article_match_keys WHERE key_type = %s AND key_value = %s",
            (key_type, key_value),
        )


@pytest.fixture(scope="module")
def cassandra():
    session = get_cassandra_session()
    _clear_snapshots(session, _DISCORD_SERVICE)
    _clear_snapshots(session, _X_SERVICE)
    _clear_match_keys(session)
    yield session


def _ingest_v1(cassandra: Session) -> str:
    """Foundation Discord warning → create article."""
    _clear_snapshots(cassandra, _DISCORD_SERVICE)
    print("Deleted snapshots for discord:algorand-foundation:announcements")
    with patch("app.modules.newspaper.publish_daily_guard.reserve_publish_slot") as mock_reserve:
        mock_reserve.return_value = (True, "test")
        with patch("app.modules.newspaper.publish_daily_guard.assert_publish_allowed"):
            result = ingest_publish_signal(
                service_id=_DISCORD_SERVICE,
                display_name="Algorand Foundation",
                source_url="https://discord.com/channels/algorand-foundation/123456789",
                page_title="Scam alert",
                page_text=f"Warning: algoblow.com is a scam. [v1 {uuid.uuid4().hex[:8]}]",
                source_kind="discord",
                match_kind="domain",
                match_value="algoblow.com",
                txid="tx1",
                round_num=1000,
            )
    assert result["status"] == "enqueued", f"Got: {result}"
    queue_id = result["queue_id"]

    queued = _load_queue_row(cassandra, queue_id)
    with patch("app.modules.newspaper.publish_daily_guard.reserve_publish_slot") as mock_reserve:
        mock_reserve.return_value = (True, "test")
        with patch("app.modules.newspaper.publish_daily_guard.assert_publish_allowed"):
            publish_result = publish_from_queued_row(queued, publish_tier=PublishTier.STANDARD)
    assert publish_result["status"] == "published", f"Got: {publish_result}"
    article_id = publish_result["article_id"]
    assert uuid.UUID(article_id)

    # Verify match keys
    keys = build_match_keys(
        service_id=_DISCORD_SERVICE,
        page_text="Warning: algoblow.com is a scam.",
        source_url="https://discord.com/channels/algorand-foundation/123456789",
    )
    stored_keys = cassandra.execute(
        "SELECT key_type, key_value FROM article_match_keys_by_article WHERE article_id = %s",
        (uuid.UUID(article_id),),
    )
    stored = {(row.key_type, row.key_value) for row in stored_keys}
    expected = {(k, v) for k, v in keys}
    assert expected.issubset(stored)
    return article_id


def _ingest_v2(article_id: str, cassandra: Session) -> None:
    """Community X post → edit article."""
    _clear_snapshots(cassandra, _X_SERVICE)
    with open("tests/fixtures/algoblow_d13_alert.txt", encoding="utf-8") as f:
        x_text = f.read()

    with patch("app.modules.newspaper.publish_daily_guard.reserve_publish_slot") as mock_reserve:
        mock_reserve.return_value = (True, "test")
        with patch("app.modules.newspaper.publish_daily_guard.assert_publish_allowed"):
            result = ingest_publish_signal(
                service_id=_X_SERVICE,
                display_name="D13",
                source_url="https://x.com/d13_co/status/2060386210732761317",
                page_title="algoblow victim addresses",
                page_text=x_text + " [v2 " + str(uuid.uuid4())[:8] + "]",
                source_kind="x",
                match_kind="domain",
                match_value="algoblow.com",
                txid="tx2",
                round_num=1001,
                publish_mode="edit",
                linked_article_id=article_id,
            )
    assert result["status"] == "enqueued", f"Got: {result}"
    queue_id = result["queue_id"]

    queued = _load_queue_row(cassandra, queue_id)
    assert queued.payload.get("publish_mode") == "edit", f"Got: {queued.payload}"
    assert queued.payload.get("linked_article_id") == article_id

    publish_result = publish_from_queued_row(queued)
    assert publish_result["status"] == "edited", f"Got: {publish_result}"
    assert publish_result["article_id"] == article_id


# Rekeyed address from tests/fixtures/algoblow_d13_alert.txt — only in the v2 signal.
_V2_TOKEN = "A43BSFDDZGPEVB2XUUX652OOHNHRA3OZVP4FNM7MF5TDOCUFZWGLS7MR6A"


def test_algoblow_e2e(cassandra: Session) -> None:
    """Ingest v1 → publish → ingest v2 → edit → versions."""
    # v1: create
    article_id = _ingest_v1(cassandra)
    v1_article = get_article(article_id)
    assert v1_article is not None
    assert "algoblow.com" in v1_article.body
    assert _V2_TOKEN not in v1_article.body

    # v2: edit
    _ingest_v2(article_id, cassandra)
    v2_article = get_article(article_id)
    assert v2_article is not None
    assert "algoblow.com" in v2_article.body
    assert _V2_TOKEN in v2_article.body
    assert "Updated" in v2_article.body

    # Versions: v1 snapshot saved before the edit, v2 written after it
    versions = sorted(list_versions(article_id), key=lambda v: v.version)
    assert len(versions) == 2, f"Got: {versions}"
    assert versions[0].version == 1
    assert versions[0].edit_reason == "before_edit"
    assert "algoblow.com" in versions[0].body
    assert _V2_TOKEN not in versions[0].body
    assert versions[1].version == 2
    assert versions[1].edit_reason.startswith("follow_up_ingest")


if __name__ == "__main__":
    # Run with: python -m pytest tests/test_algoblow_e2e.py -q
    session = get_cassandra_session()
    test_algoblow_e2e(session)