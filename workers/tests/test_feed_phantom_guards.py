"""Feed-projection writes must never upsert partial phantom rows.

Incident 2026-07-15: Cassandra UPDATE/INSERT are upserts. Any feed write that
runs against an absent PK (held article never published to the feed, row
deleted by an admin, or row MOVED by a recompose re-publish re-stamping
published_at) used to create a partial row with null service_id — which the
feed API's defensive filter silently hides. Guards under test:

- FeedStmts.UPDATE_IMAGE / UPDATE_TRANSLATIONS and the articles_by_id
  translations update are LWT ``IF EXISTS`` — a missing row is a no-op, not
  a phantom.
- update_article's feed write is a COMPLETE row (every projection column),
  so even an upsert-resurrection yields a fully valid row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.statements import ArticleStmts, FeedStmts
from app.modules.newspaper.article_store import (
    update_article,
    update_article_image,
    update_article_translations,
)

_PUBLISHED_AT = datetime(2026, 7, 14, 18, 52, 10, 629000, tzinfo=UTC)


def _stmt_cql(registry: type, name: str) -> str:
    return registry.__dict__[name].cql


def test_feed_and_article_mutation_statements_are_conditional() -> None:
    """The feed/article translation and image update statements all carry an "IF EXISTS" LWT guard."""
    assert _stmt_cql(FeedStmts, "UPDATE_IMAGE").endswith("IF EXISTS")
    assert _stmt_cql(FeedStmts, "UPDATE_TRANSLATIONS").endswith("IF EXISTS")
    assert _stmt_cql(ArticleStmts, "UPDATE_TRANSLATIONS").endswith("IF EXISTS")


def _session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    return session


def _row(aid: UUID) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.service_id = "svc-1"
    row.source_url = "https://example.com/src"
    row.image_url = "https://example.com/hero.png"
    row.title = "T"
    row.summary = "S"
    row.body = "B"
    row.published_at = _PUBLISHED_AT
    row.trigger_txid = ""
    row.trigger_round = 0
    row.prompt_version = ""
    row.translations = None
    row.tags = ["tag"]
    row.first_published_at = None
    return row


def test_update_article_image_missing_feed_row_is_a_noop_not_a_phantom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing feed row on image update is a conditional no-op, never a partial phantom insert."""
    aid = uuid4()
    session = _session(monkeypatch)
    result = MagicMock()
    result.one.return_value = _row(aid)
    result.was_applied = False  # LWT declined: no row at this PK
    session.execute.return_value = result

    assert update_article_image(str(aid), "https://example.com/new.png")

    feed_updates = [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str)
        and stmt.startswith("UPDATE algorand_platform.articles_feed SET image_url")
    ]
    assert len(feed_updates) == 1
    assert feed_updates[0][0].endswith("IF EXISTS")


def test_update_article_translations_dropped_when_article_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skips the feed translations write entirely once the article-row LWT declines (article deleted)."""
    aid = uuid4()
    session = _session(monkeypatch)
    result = MagicMock()
    result.one.return_value = _row(aid)
    result.was_applied = False  # article row gone
    session.execute.return_value = result

    assert not update_article_translations(str(aid), {"fr": "{}"})

    # The feed write must never run once the article-level LWT declined.
    feed_updates = [
        stmt
        for stmt, _params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str)
        and stmt.startswith("UPDATE algorand_platform.articles_feed SET translations")
    ]
    assert feed_updates == []


def test_update_article_translations_survives_missing_feed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still reports success updating translations when the article row exists but the feed row is absent."""
    aid = uuid4()
    session = _session(monkeypatch)

    def execute(stmt: str, _params: tuple | None = None) -> MagicMock:
        result = MagicMock()
        result.one.return_value = _row(aid)
        # Article row exists; feed row is absent (held/moved by recompose).
        result.was_applied = not (isinstance(stmt, str) and "articles_feed" in stmt)
        return result

    session.execute.side_effect = execute

    assert update_article_translations(str(aid), {"fr": "{}"})


def test_update_article_writes_complete_feed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """update_article's feed INSERT carries every projection column, including image/source URL and the original published_at."""
    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = _row(aid)

    assert update_article(article_id=str(aid), title="New", summary="NS", body="NB")

    inserts = [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list)
        if isinstance(stmt, str) and stmt.startswith("INSERT INTO algorand_platform.articles_feed")
    ]
    assert len(inserts) == 1
    stmt, params = inserts[0]
    # Complete projection: image_url and source_url must be carried, so an
    # upsert onto a deleted row cannot produce a degraded article.
    assert "image_url" in stmt
    assert "source_url" in stmt
    assert "https://example.com/hero.png" in params
    assert "https://example.com/src" in params
    # In-place snippet edit: published_at survives (only recompose re-dates).
    assert params[1] == _PUBLISHED_AT
