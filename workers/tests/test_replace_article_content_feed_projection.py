"""replace_article_content must MOVE the article's `articles` row completely on a recompose.

Recompose is a re-publish (owner policy 2026-07-15): published_at is
re-stamped to the apply time, and since published_at is part of `articles`'
partition key (status, year, published_at, article_id), the row's OLD
partition is DELETEd and a complete new one INSERTed --
transition_article_status's own delete-old-partition + insert-new-partition
dance, which carries every column not explicitly overridden.

Historically (pre article-table consolidation) this same "the write must be
COMPLETE" concern applied to a SEPARATE articles_feed projection: Cassandra
UPDATE is an upsert, and a partial feed UPDATE — title/summary/tags/image/
updated_at only — re-created a deleted feed row WITHOUT service_id/
source_url, silently hidden by the feed API's defensive filter (incident
2026-07-15). `articles` has no second, independently-written projection for
a partial write to desync from now, but the underlying discipline
(published_at moves -> delete-old + insert-COMPLETE-new, not a partial
upsert) is exactly what transition_article_status enforces, so these tests
now assert on ITS output instead of a separate feed table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.newspaper.article_store import replace_article_content

_OLD_PUBLISHED_AT = datetime(2026, 6, 14, 18, 52, 10, 629000, tzinfo=UTC)

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at", "views",
)  # fmt: skip


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
    row.image_url = None
    row.composed_by_model = None
    row.deleted_at = None
    row.status_updated_at = None
    row.interest_score = None
    row.approved_at = None
    row.updated_at = None
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


def _inserted_values(session: MagicMock) -> dict[str, object]:
    inserts = _calls_matching(session, "INSERT INTO algorand_platform.articles (")
    assert len(inserts) == 1
    _, params = inserts[0]
    return dict(zip(_ARTICLES_COLUMNS, params, strict=True))


def test_replace_deletes_old_partition_at_full_precision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletes the row's old partition keyed by its original full-precision published_at, not a reconstructed epoch."""
    aid = uuid4()
    _, session = _run_replace(monkeypatch, _article_row(aid))

    deletes = _calls_matching(session, "DELETE FROM algorand_platform.articles ")
    assert len(deletes) == 1
    _, params = deletes[0]
    # (status, year, published_at, article_id) -- the RAW full-precision
    # timestamp (never epoch-reconstructed).
    assert params == ("published", 2026, _OLD_PUBLISHED_AT, aid)


def test_replace_inserts_complete_row_at_new_published_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inserts a complete row (service_id, source_url, updated_at, first_published_at) at the new published_at."""
    aid = uuid4()
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    assert new_published_at is not None

    values = _inserted_values(session)
    assert values["article_id"] == aid
    assert values["published_at"] == new_published_at
    assert values["year"] == new_published_at.year
    assert values["status"] == "published"
    assert values["service_id"] == "editorial-brief:53016f2f"
    assert values["source_url"] == "editorial://brief/53016f2f"
    # First recompose: first_published_at is seeded with the ORIGINAL date.
    assert values["first_published_at"] == _OLD_PUBLISHED_AT
    # New content, cleared translations (a recompose invalidates every
    # existing translation of the old prose).
    assert values["title"] == "New title"
    assert values["translations"] is None


def test_replace_restamps_published_at_to_apply_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restamps published_at to the recompose apply time while preserving the original first_published_at."""
    aid = uuid4()
    before = datetime.now(tz=UTC)
    new_published_at, session = _run_replace(monkeypatch, _article_row(aid))
    after = datetime.now(tz=UTC)

    assert new_published_at is not None
    assert before <= new_published_at <= after
    assert new_published_at != _OLD_PUBLISHED_AT

    values = _inserted_values(session)
    assert values["published_at"] == new_published_at
    assert values["first_published_at"] == _OLD_PUBLISHED_AT


def test_second_recompose_preserves_original_first_published_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """first_published_at is set ONCE (first recompose) and carried verbatim afterwards — a weekly refresh chain must not walk the original date forward one recompose at a time."""
    aid = uuid4()
    row = _article_row(aid)
    original = datetime(2026, 5, 1, 12, 0, 0, 111000, tzinfo=UTC)
    row.first_published_at = original  # already recomposed once before
    _, session = _run_replace(monkeypatch, row)

    assert _inserted_values(session)["first_published_at"] == original


def test_replace_carries_slug_onto_the_new_partition(monkeypatch: pytest.MonkeyPatch) -> None:
    """The DELETE+INSERT here creates a genuinely new partition row with no prior slug for a plain UPDATE to leave untouched, so the new row's slug must be carried explicitly (same discipline as the pre-consolidation feed-row slug carry, root-caused live 2026-08-10)."""
    aid = uuid4()
    _, session = _run_replace(monkeypatch, _article_row(aid))

    slug_updates = _calls_matching(session, "UPDATE algorand_platform.articles SET slug")
    assert len(slug_updates) == 1
    _, params = slug_updates[0]
    assert params[0] == "old-title-slug"
    assert params[4] == aid


def test_replace_skips_slug_carry_when_article_has_no_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    """An article that never claimed a slug issues no slug write — nothing to carry, and no accidental empty-string slug."""
    aid = uuid4()
    row = _article_row(aid)
    row.slug = None
    _, session = _run_replace(monkeypatch, row)

    slug_updates = _calls_matching(session, "UPDATE algorand_platform.articles SET slug")
    assert slug_updates == []


def test_replace_insert_binds_none_for_empty_source_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Binds source_url back to None on the new row when it was coerced to an empty string."""
    aid = uuid4()
    row = _article_row(aid)
    row.source_url = None  # get_article coerces to "" — must bind back to None
    _, session = _run_replace(monkeypatch, row)

    assert _inserted_values(session)["source_url"] is None


def test_replace_on_a_drafted_article_never_moves_the_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-11 (before it could bite live on Lumi Rogue, held in draft): a recompose approved for a DRAFTED live article must update content but never re-stamp published_at or move it off status='draft' -- doing so would silently un-draft a withdrawn article back onto the public feed, exactly the failure already fixed for the admin content-edit path."""
    aid = uuid4()
    row = _article_row(aid)
    row.status = "draft"
    result, session = _run_replace(monkeypatch, row)

    assert result == _OLD_PUBLISHED_AT  # published_at is NOT re-stamped
    assert _calls_matching(session, "DELETE FROM algorand_platform.articles ") == []
    assert _calls_matching(session, "INSERT INTO algorand_platform.articles (") == []
    content_updates = _calls_matching(session, "UPDATE algorand_platform.articles SET title")
    assert len(content_updates) == 1
    _, params = content_updates[0]
    assert params[0] == "New title"
    # A plain in-place UPDATE keyed on the row's CURRENT (unchanged)
    # partition -- published_at only appears in the WHERE clause (the
    # partition key), never re-derived or moved, unlike the real-recompose
    # branch above (DELETE-old-partition + INSERT-new-partition).
    assert params[-4:] == ("draft", 2026, _OLD_PUBLISHED_AT, aid)


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
