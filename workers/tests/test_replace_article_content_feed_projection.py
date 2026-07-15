"""replace_article_content must MOVE the feed row completely on a recompose.

Recompose is a re-publish (owner policy 2026-07-15): published_at is
re-stamped to the apply time, and since published_at is part of the feed PK
the old feed row is DELETEd and a complete new row INSERTed.

Incident 2026-07-15 (the reason the insert must be COMPLETE): Cassandra
UPDATE is an upsert. The previous implementation's partial feed UPDATE —
title/summary/tags/image/updated_at only — re-created a deleted feed row
WITHOUT service_id/source_url. The feed API's defensive filter
(news_service.list_feed_page: `if a.service_id and a.title`) then silently
hid the article from every feed response while articles_by_id and the
article detail endpoint stayed perfectly healthy, which made the symptom
look like a server-side caching bug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.newspaper.article_store import replace_article_content

_OLD_PUBLISHED_AT = datetime(2026, 6, 14, 18, 52, 10, 629000)


def _article_row(aid) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.service_id = "editorial-brief:53016f2f"
    row.source_url = "editorial://brief/53016f2f"
    row.title = "Old title"
    row.summary = "Old summary"
    row.body = "Old body"
    row.published_at = _OLD_PUBLISHED_AT
    row.trigger_txid = ""
    row.trigger_round = 0
    row.prompt_version = ""
    row.translations = None
    row.tags = ["nft"]
    return row


def _run_replace(monkeypatch, row) -> tuple[object, MagicMock]:
    # Resolve _Stmt descriptors to their raw CQL so calls are identifiable.
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: session
    )
    session.execute.return_value.one.return_value = row
    result = replace_article_content(
        article_id=str(row.article_id),
        title="New title",
        summary="New summary",
        body="New body",
        tags=["nft", "updated"],
        image_url="https://example.com/hero.png",
    )
    return result, session


def _calls_matching(session: MagicMock, prefix: str) -> list[tuple]:
    return [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str) and stmt.startswith(prefix)
    ]


def test_replace_deletes_old_feed_row_at_full_precision(monkeypatch) -> None:
    aid = uuid4()
    _, session = _run_replace(monkeypatch, _article_row(aid))

    deletes = _calls_matching(
        session, "DELETE FROM algorand_platform.articles_feed"
    )
    assert len(deletes) == 1
    _, params = deletes[0]
    # Old bucket + the RAW full-precision timestamp (never epoch-reconstructed).
    assert params == ("2026-06", _OLD_PUBLISHED_AT, aid)


def test_replace_inserts_complete_feed_row_at_new_published_at(
    monkeypatch,
) -> None:
    aid = uuid4()
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    assert new_published_at is not None

    inserts = _calls_matching(
        session, "INSERT INTO algorand_platform.articles_feed"
    )
    assert len(inserts) == 1
    stmt, params = inserts[0]
    # Every projection column must be present — a partial row is a phantom
    # the feed API silently hides.
    assert "service_id" in stmt
    assert "source_url" in stmt
    assert "updated_at" in stmt
    bucket, published_at, row_aid, service_id, _title, *_rest = params
    assert row_aid == aid
    assert published_at == new_published_at
    assert bucket == new_published_at.strftime("%Y-%m")
    assert service_id == "editorial-brief:53016f2f"
    assert params[8] == "editorial://brief/53016f2f"  # source_url


def test_replace_restamps_published_at_to_apply_time(monkeypatch) -> None:
    aid = uuid4()
    before = datetime.now(tz=UTC)
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    after = datetime.now(tz=UTC)

    assert new_published_at is not None
    assert before <= new_published_at <= after

    updates = _calls_matching(
        session, "UPDATE algorand_platform.articles_by_id SET title"
    )
    assert len(updates) == 1
    stmt, params = updates[0]
    assert "published_at = ?" in stmt
    # (title, summary, body, tags, image, published_at, updated_at, aid)
    assert params[5] == new_published_at


def test_replace_feed_insert_binds_null_for_empty_source_url(
    monkeypatch,
) -> None:
    aid = uuid4()
    row = _article_row(aid)
    row.source_url = None  # get_article coerces to "" — must bind back to None
    _, session = _run_replace(monkeypatch, row)

    inserts = _calls_matching(
        session, "INSERT INTO algorand_platform.articles_feed"
    )
    assert len(inserts) == 1
    assert inserts[0][1][8] is None  # source_url
