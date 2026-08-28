"""Admin-only draft toggle: flip a live article's `articles` row between status='draft'/'published' reversibly, without touching its stored content."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore

# `articles`' column order (see algorand_shared.article_transitions._ARTICLES_COLUMNS).
_ARTICLES_COLUMNS = (
    "status", "year", "published_at", "article_id", "service_id", "title", "summary", "body",
    "image_url", "tags", "source_url", "trigger_txid", "trigger_round", "slug", "translations",
    "first_published_at", "updated_at", "prompt_version", "composed_by_model",
    "deleted_at", "status_updated_at", "interest_score", "approved_at", "views",
)  # fmt: skip


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    def __init__(self, article_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._article_row = article_row
        self.articles_inserts: list[tuple] = []
        self.articles_deletes: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "FROM algorand_platform.articles WHERE article_id = ?" in q:
            return _Result(self._article_row)
        if q.startswith("INSERT INTO algorand_platform.articles ("):
            self.articles_inserts.append(tuple(params))
        elif q.startswith("DELETE FROM algorand_platform.articles "):
            self.articles_deletes.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def _feed_row(article_id: Any, *, slug: str | None = "a-slug", status: str = "published") -> Any:  # noqa: ANN401 -- duck-typed Cassandra row, no formal class
    published_at = datetime.now(tz=UTC)
    values: dict[str, object] = dict.fromkeys(_ARTICLES_COLUMNS)
    values.update(
        status=status,
        year=published_at.year,
        published_at=published_at,
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        body="",
        tags=["a", "b"],
        image_url="https://example.com/img.png",
        source_url="https://example.com/",
        trigger_txid="",
        trigger_round=0,
        slug=slug,
    )
    return SimpleNamespace(**values)


def test_set_draft_true_moves_the_articles_row_to_status_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drafting a live article transitions its `articles` row to status='draft', published_at unchanged (not a republish), content untouched."""
    article_id = uuid4()
    row = _feed_row(article_id, status="published")
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr("app.core.typesense_client.delete_article_document", lambda *a, **kw: None)  # noqa: ARG005
    # get_article's own full-row read isn't what this test is about -- stub it
    # so the fake session's simplified article row doesn't need every column
    # NewsService's get() path expects too.
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    result = AdminCassandraStore().set_article_draft(str(article_id), True)

    assert result is not None
    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "draft"
    assert values["published_at"] == row.published_at  # unchanged -- not a republish
    assert values["title"] == "Title"  # content carried forward, untouched
    assert fake.articles_deletes == [("published", row.year, row.published_at, article_id)]


def test_set_draft_false_restores_status_published_without_restamping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restoring transitions the SAME `articles` row back to status='published' with its ORIGINAL published_at (not re-stamped) -- a restore, not a republish."""
    article_id = uuid4()
    row = _feed_row(article_id, status="draft")
    fake = _FakeSession(row)
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr(
        "app.core.typesense_client.upsert_article_document", lambda **kw: None  # noqa: ARG005
    )
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    AdminCassandraStore().set_article_draft(str(article_id), False)

    assert len(fake.articles_inserts) == 1
    values = dict(zip(_ARTICLES_COLUMNS, fake.articles_inserts[0], strict=True))
    assert values["status"] == "published"
    assert values["published_at"] == row.published_at  # unchanged -- a restore, not a republish
    assert values["slug"] == "a-slug"  # carried forward, untouched
    assert fake.articles_deletes == [("draft", row.year, row.published_at, article_id)]


def test_set_draft_on_missing_article_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to toggle -- no writes at all."""
    fake = _FakeSession(None)
    _patch(monkeypatch, fake)

    result = AdminCassandraStore().set_article_draft(str(uuid4()), True)

    assert result is None
    assert fake.articles_inserts == []


def test_list_draft_articles_reads_articles_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-08-24: enumeration comes from `articles` (status='draft'), not the legacy `draft_articles` index -- authoritative status means no ghost-row concept anymore. status_updated_at is the drafted_at equivalent."""
    live_id = uuid4()
    live_row = SimpleNamespace(
        article_id=live_id,
        status_updated_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
    fake = _FakeSession(None)
    orig_execute = fake.execute

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if "FROM algorand_platform.articles WHERE status = ? AND year = ?" in q:
            # Only the current year (the test's default clock) has a row.
            return [live_row] if params[1] == datetime.now(tz=UTC).year else []
        return orig_execute(query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)

    live_article = SimpleNamespace(
        article_id=str(live_id),
        title="Live draft (current title)",
        source_url="https://example.com/live",
        draft=True,
    )
    monkeypatch.setattr(
        "app.modules.news.stores.cassandra.CassandraArticleStore.get_many",
        lambda self, ids: {str(live_id): live_article},  # noqa: ARG005
    )

    items = AdminCassandraStore().list_draft_articles()

    assert [it["article_id"] for it in items] == [str(live_id)]
    assert items[0]["title"] == "Live draft (current title)"
    assert items[0]["drafted_at"] == "2026-08-17T00:00:00+00:00"


def test_list_draft_articles_returns_empty_with_no_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    """No status='draft' rows in any scanned year -- no CassandraArticleStore call needed, empty list back."""
    fake = _FakeSession(None)

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if "FROM algorand_platform.articles WHERE status = ? AND year = ?" in q:
            return []
        return fake.__class__.execute(fake, query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)

    assert AdminCassandraStore().list_draft_articles() == []


def test_list_draft_articles_bounds_rows_across_years(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-08-28 perf audit: the drafts listing must not fetch unbounded rows.

    It must not re-fetch every status='draft' row across all 3 scanned
    year-partitions unbounded -- each year's query now carries a real LIMIT
    (the query's 3rd bind param), and the function stops assembling once the
    overall scan_limit is hit, never even querying a further year's
    partition once the cap is already reached.
    """
    fake = _FakeSession(None)
    calls: list[tuple] = []
    current_year = datetime.now(tz=UTC).year
    # More rows available per year than the default scan_limit (500), so the bound is
    # what actually stops the read, not running out of data.
    per_year_available = {current_year: 400, current_year - 1: 400, current_year - 2: 400}

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if "FROM algorand_platform.articles WHERE status = ? AND year = ?" in q:
            calls.append(tuple(params))
            _status, year, limit_param = params
            n = min(limit_param, per_year_available.get(year, 0))
            return [
                SimpleNamespace(
                    article_id=uuid4(), status_updated_at=datetime(2026, 8, 17, tzinfo=UTC)
                )
                for _ in range(n)
            ]
        return fake.__class__.execute(fake, query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)
    monkeypatch.setattr(
        "app.modules.news.stores.cassandra.CassandraArticleStore.get_many",
        lambda self, ids: {},  # noqa: ARG005 -- this test is about the bound, not the join
    )

    AdminCassandraStore().list_draft_articles()

    # scan_limit (500) is reached after year 1 (400) + year 2 (100 more) -- year 3's
    # partition is never queried at all.
    assert len(calls) == 2
    assert calls[0] == ("draft", current_year, 500)
    assert calls[1] == ("draft", current_year - 1, 100)


def test_list_draft_articles_scan_limit_is_a_real_query_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied scan_limit is threaded through as the CQL LIMIT.

    Not just a post-hoc Python-side truncation.
    """
    fake = _FakeSession(None)
    calls: list[tuple] = []

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if "FROM algorand_platform.articles WHERE status = ? AND year = ?" in q:
            calls.append(tuple(params))
            return []
        return fake.__class__.execute(fake, query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)

    assert AdminCassandraStore().list_draft_articles(scan_limit=7) == []

    assert calls
    assert calls[0][2] == 7


def test_restore_claims_a_slug_when_the_row_never_had_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un-drafting a row with slug=NULL claims one on the way back to status='published' -- a row that reached 'draft' without ever being slugged (a pre-fix slug=NULL victim, or a never-published row toggled via the raw API) must not go onto the public feed serving a bare-UUID URL (the 2026-08-27 bug class)."""
    article_id = uuid4()
    row = _feed_row(article_id, status="draft", slug=None)
    fake = _FakeSession(row)
    slug_sets: list[tuple] = []
    orig_execute = fake.execute

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if q.startswith("UPDATE algorand_platform.articles SET slug = ?"):
            slug_sets.append(tuple(params))
        return orig_execute(query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr(
        "app.core.typesense_client.upsert_article_document", lambda **kw: None  # noqa: ARG005
    )
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    AdminCassandraStore().set_article_draft(str(article_id), False)

    # The real slugify ran against the fake session: "Title" -> "title",
    # written onto the `articles` row via ArticlesStmts.SET_SLUG.
    assert [p[0] for p in slug_sets] == ["title"]


def test_restore_leaves_an_existing_slug_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un-drafting a previously-published row is a no-op for slugs -- the permanent URL is never recomputed."""
    article_id = uuid4()
    row = _feed_row(article_id, status="draft", slug="a-slug")
    fake = _FakeSession(row)
    slug_sets: list[tuple] = []
    orig_execute = fake.execute

    def execute(query: str, params: tuple = ()) -> Any:  # noqa: ANN401
        q = " ".join(str(query).split())
        if q.startswith("UPDATE algorand_platform.articles SET slug = ?"):
            slug_sets.append(tuple(params))
        return orig_execute(query, params)

    fake.execute = execute  # type: ignore[method-assign]
    _patch(monkeypatch, fake)
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *a, **kw: None)  # noqa: ARG005
    monkeypatch.setattr(
        "app.core.typesense_client.upsert_article_document", lambda **kw: None  # noqa: ARG005
    )
    monkeypatch.setattr(AdminCassandraStore, "get_article", lambda self, aid: object())  # noqa: ARG005

    AdminCassandraStore().set_article_draft(str(article_id), False)

    assert slug_sets == []
