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
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.newspaper.article_store import replace_article_content

_OLD_PUBLISHED_AT = datetime(2026, 6, 14, 18, 52, 10, 629000, tzinfo=UTC)


def _article_row(aid: UUID) -> MagicMock:
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
    row.first_published_at = None  # never recomposed before
    row.slug = "old-title-slug"
    row.status = "published"
    row.year = 2026
    return row


def _run_replace(monkeypatch: pytest.MonkeyPatch, row: Any) -> tuple[object, MagicMock]:  # noqa: ANN401 -- duck-typed Cassandra row/result
    # Resolve _Stmt descriptors to their raw CQL so calls are identifiable.
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
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
    # auto_link_glossary_terms (called at the top of replace_article_content)
    # issues its own parameterless session.execute(query) against the shared
    # mock session, a 1-arg call this helper's (stmt, params) unpacking never
    # accounted for -- skip anything that isn't a real (statement, params) pair.
    return [
        (stmt, params)
        for stmt, params in (c.args for c in session.execute.call_args_list if len(c.args) == 2)
        if isinstance(stmt, str) and stmt.startswith(prefix)
    ]


def test_replace_deletes_old_feed_row_at_full_precision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletes the old feed row keyed by its original full-precision published_at, not a reconstructed epoch."""
    aid = uuid4()
    _, session = _run_replace(monkeypatch, _article_row(aid))

    deletes = _calls_matching(session, "DELETE FROM algorand_platform.articles_feed")
    assert len(deletes) == 1
    _, params = deletes[0]
    # Old bucket + the RAW full-precision timestamp (never epoch-reconstructed).
    assert params == ("2026-06", _OLD_PUBLISHED_AT, aid)


def test_replace_inserts_complete_feed_row_at_new_published_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inserts a complete feed row (service_id, source_url, updated_at, first_published_at) at the new published_at."""
    aid = uuid4()
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    assert new_published_at is not None

    inserts = _calls_matching(session, "INSERT INTO algorand_platform.articles_feed")
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
    # First recompose: first_published_at is seeded with the ORIGINAL date.
    assert params[9] == _OLD_PUBLISHED_AT


def test_replace_restamps_published_at_to_apply_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restamps published_at to the recompose apply time while preserving the original first_published_at."""
    aid = uuid4()
    before = datetime.now(tz=UTC)
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    after = datetime.now(tz=UTC)

    assert new_published_at is not None
    assert before <= new_published_at <= after

    updates = _calls_matching(session, "UPDATE algorand_platform.articles_by_id SET title")
    assert len(updates) == 1
    stmt, params = updates[0]
    assert "published_at = ?" in stmt
    assert "first_published_at = ?" in stmt
    # (title, summary, body, tags, image, published_at, first_published_at,
    #  updated_at, aid)
    assert params[5] == new_published_at
    assert params[6] == _OLD_PUBLISHED_AT


def test_second_recompose_preserves_original_first_published_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """first_published_at is set ONCE (first recompose) and carried verbatim afterwards — a weekly refresh chain must not walk the original date forward one recompose at a time."""
    aid = uuid4()
    row = _article_row(aid)
    original = datetime(2026, 5, 1, 12, 0, 0, 111000, tzinfo=UTC)
    row.first_published_at = original  # already recomposed once before
    _, session = _run_replace(monkeypatch, row)

    inserts = _calls_matching(session, "INSERT INTO algorand_platform.articles_feed")
    assert inserts[0][1][9] == original
    updates = _calls_matching(session, "UPDATE algorand_platform.articles_by_id SET title")
    assert updates[0][1][6] == original


def test_daily_cap_ignores_recompose_republishes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recomposed article re-enters today's published_at window but must not consume a daily publish slot — count by first_published_at."""
    from app.modules.newspaper import article_store

    day_start = 1_784_073_600  # 2026-07-14 00:00 UTC
    rows = [
        # Genuinely new article today.
        article_store.FeedArticleRow(
            article_id="a",
            service_id="s1",
            title="t",
            summary="",
            published_at_epoch=day_start + 3600,
        ),
        # Recomposed today, FIRST published two weeks ago — not a new publish.
        article_store.FeedArticleRow(
            article_id="b",
            service_id="s2",
            title="t",
            summary="",
            published_at_epoch=day_start + 7200,
            first_published_at_epoch=day_start - 14 * 86400,
        ),
        # Old article, untouched.
        article_store.FeedArticleRow(
            article_id="c",
            service_id="s3",
            title="t",
            summary="",
            published_at_epoch=day_start - 86400,
        ),
    ]
    monkeypatch.setattr(article_store, "list_feed_articles", lambda *, limit=500: rows)  # noqa: ARG005 -- name must match the real callee's keyword arg
    assert article_store.count_articles_published_on_utc_day(day_start_epoch=day_start) == 1


def test_replace_carries_slug_onto_the_new_feed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused live 2026-08-10 (GSC 'Page with redirect': 545 pages): the DELETE+INSERT here creates a genuinely new feed row with no prior slug for Cassandra to leave untouched, so every recompose silently dropped the feed-visible slug — the homepage reads slug from THIS projection, not articles_by_id, and fell back to uuid-form links, sending every recomposed article's readers (and Google) through an extra 301. The new row's slug must be carried explicitly."""
    aid = uuid4()
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))

    slug_updates = _calls_matching(session, "UPDATE algorand_platform.articles_feed SET slug")
    assert len(slug_updates) == 1
    _, params = slug_updates[0]
    assert params == ("old-title-slug", new_published_at.strftime("%Y-%m"), new_published_at, aid)


def test_replace_skips_slug_carry_when_article_has_no_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """An article that never claimed a slug (pre-migration-056 row) issues no slug write — nothing to carry, and no accidental empty-string slug."""
    aid = uuid4()
    row = _article_row(aid)
    row.slug = None
    _, session = _run_replace(monkeypatch, row)

    slug_updates = _calls_matching(session, "UPDATE algorand_platform.articles_feed SET slug")
    assert slug_updates == []


def test_replace_feed_insert_binds_null_for_empty_source_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binds source_url back to None on the feed insert when it was coerced to an empty string."""
    aid = uuid4()
    row = _article_row(aid)
    row.source_url = None  # get_article coerces to "" — must bind back to None
    _, session = _run_replace(monkeypatch, row)

    inserts = _calls_matching(session, "INSERT INTO algorand_platform.articles_feed")
    assert len(inserts) == 1
    assert inserts[0][1][8] is None  # source_url


def test_replace_on_a_drafted_article_never_touches_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-08-11 (before it could bite live on Lumi Rogue, held in draft): a recompose approved for a DRAFTED live article must update content but never rewrite the feed row or re-stamp published_at -- doing so would silently un-draft a withdrawn article back onto the public feed, exactly the failure already fixed for the admin content-edit path."""
    aid = uuid4()
    row = _article_row(aid)
    row.status = "draft"
    result, session = _run_replace(monkeypatch, row)

    assert result == _OLD_PUBLISHED_AT  # published_at is NOT re-stamped
    assert _calls_matching(session, "DELETE FROM algorand_platform.articles_feed") == []
    assert _calls_matching(session, "INSERT INTO algorand_platform.articles_feed") == []
    content_updates = _calls_matching(
        session, "UPDATE algorand_platform.articles_by_id SET title"
    )
    assert len(content_updates) == 1
    stmt, params = content_updates[0]
    assert "published_at" not in stmt  # the timestamp-preserving statement, not the full one
    assert params[0] == "New title"
