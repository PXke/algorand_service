"""replace_article_content must keep the articles_feed projection COMPLETE.

Incident 2026-07-15: Cassandra UPDATE is an upsert. When an article's feed row
had been deleted (admin queue clear), replace_article_content's feed update —
which only set title/summary/tags/image_url/updated_at — re-created the row
WITHOUT service_id/source_url. The feed API's defensive filter
(news_service.list_feed_page: `if a.service_id and a.title`) then silently hid
the article from every feed response while articles_by_id and the article
detail endpoint stayed perfectly healthy, which made the symptom look like a
server-side caching bug. The fix: the feed UPDATE re-asserts service_id and
source_url from the existing article on every content swap, so a
resurrected-by-upsert row is always complete.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.newspaper.article_store import replace_article_content


def _article_row(aid) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.service_id = "editorial-brief:53016f2f"
    row.source_url = "editorial://brief/53016f2f"
    row.title = "Old title"
    row.summary = "Old summary"
    row.body = "Old body"
    row.published_at = datetime(2026, 7, 14, 18, 52, 10, 629000)
    row.trigger_txid = ""
    row.trigger_round = 0
    row.prompt_version = ""
    row.translations = None
    row.tags = ["nft"]
    return row


def test_replace_article_content_reasserts_feed_service_id_and_source_url(
    monkeypatch,
) -> None:
    # Resolve _Stmt descriptors to their raw CQL so calls are identifiable.
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: session
    )
    aid = uuid4()
    session.execute.return_value.one.return_value = _article_row(aid)

    assert replace_article_content(
        article_id=str(aid),
        title="New title",
        summary="New summary",
        body="New body",
        tags=["nft", "updated"],
        image_url="https://example.com/hero.png",
    )

    feed_updates = [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str)
        and stmt.startswith("UPDATE algorand_platform.articles_feed SET title")
    ]
    assert len(feed_updates) == 1
    stmt, params = feed_updates[0]
    # The statement itself must carry both projection columns — this is what
    # makes an upsert-after-delete produce a complete row instead of a phantom.
    assert "service_id = ?" in stmt
    assert "source_url = ?" in stmt
    assert params[4] == "editorial-brief:53016f2f"
    assert params[5] == "editorial://brief/53016f2f"


def test_replace_article_content_feed_update_binds_null_for_empty_source_url(
    monkeypatch,
) -> None:
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: session
    )
    aid = uuid4()
    row = _article_row(aid)
    row.source_url = None  # get_article coerces to "" — must bind back to None
    session.execute.return_value.one.return_value = row

    assert replace_article_content(
        article_id=str(aid),
        title="New title",
        summary="New summary",
        body="New body",
        tags=["nft"],
        image_url="",
    )

    feed_updates = [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str)
        and stmt.startswith("UPDATE algorand_platform.articles_feed SET title")
    ]
    assert len(feed_updates) == 1
    assert feed_updates[0][1][5] is None
