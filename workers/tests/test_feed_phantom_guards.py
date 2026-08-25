"""Content/image/translation writes against the consolidated `articles` table.

Historically (pre article-table consolidation) these functions wrote a
SEPARATE articles_feed projection alongside articles_by_id, and an upsert
against an absent feed-row primary key could create a partial "phantom" row
with null service_id -- silently hidden by the feed API's defensive filter
(incident 2026-07-15). `articles` is a single consolidated row per article
now: there is no second, independently-written projection for a partial
write to desync from, so that bug class is structurally impossible. These
tests instead guard the properties that still matter on the new table:

- update_article_image / update_article_translations both read the row
  first and treat a missing article as a no-op (False), never an upsert
  that resurrects a deleted article.
- update_article writes a complete content update (title/summary/body/tags/
  image_url/updated_at) keyed on the row's current partition
  (status/year/published_at), which it also read fresh rather than assuming.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

_PUBLISHED_AT = datetime(2026, 7, 14, 18, 52, 10, 629000, tzinfo=UTC)


def _session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    return session


def _row(aid: UUID) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.status = "published"
    row.year = 2026
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
    row.slug = "existing-slug"
    return row


def _writes(session: MagicMock, prefix: str) -> list[tuple]:
    return [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list if len(c.args) == 2)
        if isinstance(stmt, str) and stmt.startswith(prefix)
    ]


def test_update_article_image_missing_article_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `articles` row at all (bad id, or deleted between enqueue and run) -- reported as failure, no write attempted."""
    from app.modules.newspaper.article_store import update_article_image

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = None

    assert not update_article_image(str(aid), "https://example.com/new.png")
    assert _writes(session, "UPDATE algorand_platform.articles SET image_url") == []


def test_update_article_image_writes_the_keyed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes image_url keyed on the row's own current status/year/published_at (its partition key)."""
    from app.modules.newspaper.article_store import update_article_image

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = _row(aid)

    assert update_article_image(str(aid), "https://example.com/new.png")

    updates = _writes(session, "UPDATE algorand_platform.articles SET image_url")
    assert len(updates) == 1
    _, params = updates[0]
    assert params == ("https://example.com/new.png", "published", 2026, _PUBLISHED_AT, aid)


def test_update_article_translations_dropped_when_article_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A translation task can outlive the article it was enqueued for -- dropping the write is correct, never an upsert that resurrects it as a translations-only phantom."""
    from app.modules.newspaper.article_store import update_article_translations

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = None

    assert not update_article_translations(str(aid), {"fr": "{}"})
    assert _writes(session, "UPDATE algorand_platform.articles SET translations") == []


def test_update_article_translations_writes_the_keyed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writes translations keyed on the row's own current status/year/published_at."""
    from app.modules.newspaper.article_store import update_article_translations

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = _row(aid)

    assert update_article_translations(str(aid), {"fr": "{}"})

    updates = _writes(session, "UPDATE algorand_platform.articles SET translations")
    assert len(updates) == 1
    _, params = updates[0]
    assert params == ({"fr": "{}"}, "published", 2026, _PUBLISHED_AT, aid)


def test_update_article_writes_complete_content_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """update_article's content UPDATE carries every edited column plus the row's own current image_url and partition key -- an in-place edit, published_at never moves."""
    from app.modules.newspaper.article_store import update_article

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = _row(aid)

    assert update_article(article_id=str(aid), title="New", summary="NS", body="NB")

    updates = _writes(session, "UPDATE algorand_platform.articles SET title")
    assert len(updates) == 1
    stmt, params = updates[0]
    assert "image_url" in stmt
    title, summary, body, _tags, image, updated_at, status, year, published_at, article_id = params
    assert (title, summary, body) == ("New", "NS", "NB")
    # Carries the row's own current image, never assumes it changed.
    assert image == "https://example.com/hero.png"
    # In-place edit: partition key (status/year/published_at) is reused
    # verbatim from the just-read row, never re-derived.
    assert (status, year, published_at, article_id) == ("published", 2026, _PUBLISHED_AT, aid)
    assert isinstance(updated_at, datetime)


def test_update_article_appends_updated_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A content edit is tagged 'updated' (once) so the frontend can flag revised stories."""
    from app.modules.newspaper.article_store import update_article

    aid = uuid4()
    session = _session(monkeypatch)
    session.execute.return_value.one.return_value = _row(aid)

    assert update_article(article_id=str(aid), title="New", summary="NS", body="NB", tags=["algorand"])

    _, params = _writes(session, "UPDATE algorand_platform.articles SET title")[0]
    tags = params[3]
    assert tags.count("updated") == 1
    assert "algorand" in tags
