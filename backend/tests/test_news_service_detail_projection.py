"""Single-article DETAIL read path (NewsService._fetch_detail/get_article/get_article_ignoring_draft_gate, and the bulk get_articles variant) projects only the translation the caller needs, instead of GET_FULL_BY_ID's complete `translations` map.

This is a 2026-08-28 follow-up to the feed-listing translated_titles fix
(migration 087, see test_article_statements_translated_titles.py in
workers/): GET_FULL_BY_ID itself is UNCHANGED and stays the read for every
other caller (writes, admin tools, backfills -- see
test_get_full_by_id_still_selects_the_full_translations_map there). This
file covers the NEW GET_BY_ID_NO_TRANSLATIONS / GET_BY_ID_WITH_TRANSLATION
statements, CassandraArticleStore.get_detail/get_many_detail, and the
NewsService wiring that prefers them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from algorand_shared.article_statements import ArticlesStmts
from conftest import patch_cassandra, stmt_cql

from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.cassandra import CassandraArticleStore
from app.modules.news.stores.memory import InMemoryArticleStore

# --------------------------------------------------------------------------- #
# Statement shape
# --------------------------------------------------------------------------- #


def test_get_by_id_no_translations_statement_omits_translations_column() -> None:
    """No lang to overlay -- the detail read shouldn't select `translations` at all."""
    cql = stmt_cql(ArticlesStmts, "GET_BY_ID_NO_TRANSLATIONS")
    assert "translations" not in cql
    # every other GET_FULL_BY_ID column stays, so the row shape is otherwise
    # identical (only the translations projection differs).
    assert "translated_titles" in cql
    assert "body" in cql
    assert "views" in cql


def test_get_by_id_with_translation_statement_projects_a_single_map_element() -> None:
    """`translations[?]` selects one language's blob, never the whole map."""
    cql = stmt_cql(ArticlesStmts, "GET_BY_ID_WITH_TRANSLATION")
    assert "translations[?] AS translations" in cql
    # one bind marker for the map key (lang), one for article_id.
    assert cql.count("?") == 2
    assert "translated_titles" in cql


def test_get_full_by_id_is_untouched_by_this_change() -> None:
    """GET_FULL_BY_ID keeps selecting the complete `translations` map -- its ~15 other call sites (writes, admin tools, backfills) still need it."""
    cql = stmt_cql(ArticlesStmts, "GET_FULL_BY_ID")
    assert "translations, " in cql
    assert "translations[" not in cql


# --------------------------------------------------------------------------- #
# CassandraArticleStore.get_detail
# --------------------------------------------------------------------------- #

_ARTICLE_ID = str(uuid4())


class _AsyncResult:
    def __init__(self, row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401
        return self._row


class _AsyncFuture:
    def __init__(self, row: Any) -> None:  # noqa: ANN401
        self._row = row

    def result(self) -> _AsyncResult:
        return _AsyncResult(self._row)


class _FakeAsyncSession:
    """Records every execute_async(stmt, params) call and returns a canned row."""

    def __init__(self, row: Any) -> None:  # noqa: ANN401
        self._row = row
        self.calls: list[tuple[str, tuple]] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute_async(self, stmt: str, params: tuple) -> _AsyncFuture:
        self.calls.append((stmt, tuple(params)))
        return _AsyncFuture(self._row)


def _row(**overrides: object) -> Any:  # noqa: ANN401
    base = {
        "status": "published",
        "published_at": datetime(2026, 8, 1, tzinfo=UTC),
        "article_id": _ARTICLE_ID,
        "service_id": "svc",
        "title": "English title",
        "summary": "English summary",
        "body": "English body",
        "trigger_txid": None,
        "trigger_round": None,
        "source_url": "https://example.com",
        "tags": ["defi"],
        "image_url": None,
        "slug": "a-slug",
        "updated_at": None,
        "first_published_at": None,
    }
    base.update(overrides)
    from types import SimpleNamespace

    return SimpleNamespace(**base)


def test_get_detail_with_lang_uses_the_map_element_projection(monkeypatch: Any) -> None:  # noqa: ANN401
    """A lang is given -> GET_BY_ID_WITH_TRANSLATION runs with (lang, article_id) bound, and the returned StoredArticle carries the {lang: blob} shape _to_detail reads."""
    blob = json.dumps({"title": "Titre FR", "summary": "Resume", "body": "Corps"})
    session = _FakeAsyncSession(_row(translations=blob))
    patch_cassandra(monkeypatch, session)

    article = CassandraArticleStore().get_detail(_ARTICLE_ID, lang="fr")

    assert len(session.calls) == 1
    stmt, params = session.calls[0]
    assert stmt == stmt_cql(ArticlesStmts, "GET_BY_ID_WITH_TRANSLATION")
    assert params[0] == "fr"
    assert str(params[1]) == _ARTICLE_ID
    assert article is not None
    # Same {lang: blob} shape NewsService._to_detail already reads via
    # article.translations[lang].
    assert article.translations == {"fr": blob}


def test_get_detail_without_lang_never_requests_translations(monkeypatch: Any) -> None:  # noqa: ANN401
    """No lang -> GET_BY_ID_NO_TRANSLATIONS runs, and the row never carries a `translations` attribute at all."""
    session = _FakeAsyncSession(_row())  # no `translations` attribute at all
    patch_cassandra(monkeypatch, session)

    article = CassandraArticleStore().get_detail(_ARTICLE_ID, lang=None)

    assert len(session.calls) == 1
    stmt, params = session.calls[0]
    assert stmt == stmt_cql(ArticlesStmts, "GET_BY_ID_NO_TRANSLATIONS")
    assert str(params[0]) == _ARTICLE_ID
    assert article is not None
    assert article.translations is None


def test_get_detail_falls_back_to_english_when_lang_has_no_stored_translation(
    monkeypatch: Any,  # noqa: ANN401
) -> None:
    """Cassandra returns NULL for a map key that isn't set -- the map-element projection comes back None, same as "never translated"."""
    session = _FakeAsyncSession(_row(translations=None))
    patch_cassandra(monkeypatch, session)

    article = CassandraArticleStore().get_detail(_ARTICLE_ID, lang="fa")
    assert article is not None
    assert article.translations is None

    # NewsService._to_detail's existing fallback logic is unchanged: no
    # entry for "fa" means English title/summary/body pass through as-is.
    detail = NewsService(store=InMemoryArticleStore())._to_detail(article, lang="fa")
    assert detail.title == "English title"
    assert detail.body == "English body"


def test_get_detail_returns_none_for_a_deleted_article(monkeypatch: Any) -> None:  # noqa: ANN401
    """Same status='deleted' -> None filter as `get()`/`_articles_row_to_stored`."""
    session = _FakeAsyncSession(_row(status="deleted"))
    patch_cassandra(monkeypatch, session)
    assert CassandraArticleStore().get_detail(_ARTICLE_ID, lang="fr") is None


def test_get_detail_returns_none_for_a_missing_row(monkeypatch: Any) -> None:  # noqa: ANN401
    """A nonexistent article_id returns None, same as `get()`."""
    session = _FakeAsyncSession(None)
    patch_cassandra(monkeypatch, session)
    assert CassandraArticleStore().get_detail(_ARTICLE_ID) is None


def test_get_detail_flags_a_draft_row_exactly_like_get(monkeypatch: Any) -> None:  # noqa: ANN401
    """status='draft' -> draft=True, same as `get()`/`_articles_row_to_stored` -- NewsService._fetch_detail's admin-only gate still works."""
    session = _FakeAsyncSession(_row(status="draft"))
    patch_cassandra(monkeypatch, session)
    article = CassandraArticleStore().get_detail(_ARTICLE_ID)
    assert article is not None
    assert article.draft is True


# --------------------------------------------------------------------------- #
# CassandraArticleStore.get_many_detail / get_many (bulk path)
# --------------------------------------------------------------------------- #


def test_get_many_detail_with_lang_uses_the_translation_statement(monkeypatch: Any) -> None:  # noqa: ANN401
    """The bulk detail read (get_articles' RSS/llms-full path) with a lang set fans out GET_BY_ID_WITH_TRANSLATION, one (lang, article_id) pair per id."""
    patch_cassandra(monkeypatch)
    aid = uuid4()
    blob = json.dumps({"title": "T-ES"})
    calls: list[tuple[str, list[tuple]]] = []

    def fake_execute_parallel_with_args(
        stmt: str, args_list: list[tuple], *, raise_on_error: bool = False
    ) -> list[tuple[bool, Any]]:
        _ = raise_on_error
        calls.append((stmt, list(args_list)))
        return [(True, _AsyncResult(_row(article_id=aid, translations=blob)))]

    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", fake_execute_parallel_with_args
    )

    out = CassandraArticleStore().get_many_detail([str(aid)], lang="es")

    assert len(calls) == 1
    stmt, args_list = calls[0]
    assert stmt == stmt_cql(ArticlesStmts, "GET_BY_ID_WITH_TRANSLATION")
    assert args_list == [("es", aid)]
    assert out[str(aid)].translations == {"es": blob}


def test_get_many_detail_without_lang_uses_the_no_translations_statement(monkeypatch: Any) -> None:  # noqa: ANN401
    """Same bulk detail read with no lang -- fans out GET_BY_ID_NO_TRANSLATIONS instead, never touching `translations`."""
    patch_cassandra(monkeypatch)
    aid = uuid4()
    calls: list[tuple[str, list[tuple]]] = []

    def fake_execute_parallel_with_args(
        stmt: str, args_list: list[tuple], *, raise_on_error: bool = False
    ) -> list[tuple[bool, Any]]:
        _ = raise_on_error
        calls.append((stmt, list(args_list)))
        return [(True, _AsyncResult(_row(article_id=aid)))]

    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", fake_execute_parallel_with_args
    )

    out = CassandraArticleStore().get_many_detail([str(aid)])

    stmt, args_list = calls[0]
    assert stmt == stmt_cql(ArticlesStmts, "GET_BY_ID_NO_TRANSLATIONS")
    assert args_list == [(aid,)]
    assert out[str(aid)].translations is None


def test_get_many_is_untouched_and_still_uses_get_full_by_id(monkeypatch: Any) -> None:  # noqa: ANN401
    """Regression guard: get_many() (no lang param at all) is a SEPARATE method from get_many_detail -- admin's list_draft_articles calls it directly and needs the complete row, same as before this change."""
    patch_cassandra(monkeypatch)
    aid = uuid4()
    calls: list[tuple[str, list[tuple]]] = []

    def fake_execute_parallel_with_args(
        stmt: str, args_list: list[tuple], *, raise_on_error: bool = False
    ) -> list[tuple[bool, Any]]:
        _ = raise_on_error
        calls.append((stmt, list(args_list)))
        return [(True, _AsyncResult(_row(article_id=aid, translations={"fr": "{}"})))]

    monkeypatch.setattr(
        "app.core.cassandra.execute_parallel_with_args", fake_execute_parallel_with_args
    )

    out = CassandraArticleStore().get_many([str(aid)])

    stmt, args_list = calls[0]
    assert stmt == stmt_cql(ArticlesStmts, "GET_FULL_BY_ID")
    assert args_list == [(aid,)]
    assert out[str(aid)].translations == {"fr": "{}"}


# --------------------------------------------------------------------------- #
# NewsService wiring: prefers get_detail/get_many_detail, forwards lang
# --------------------------------------------------------------------------- #


class _SpyStore(InMemoryArticleStore):
    """Wraps InMemoryArticleStore, recording which read method NewsService called and with what lang -- verifies the wiring without needing a real Cassandra fake."""

    def __init__(self) -> None:
        super().__init__()
        self.get_detail_calls: list[tuple[str, str | None]] = []
        self.get_many_detail_calls: list[tuple[tuple[str, ...], str | None]] = []
        self.plain_get_calls: list[str] = []
        self.plain_get_many_calls: list[tuple[str, ...]] = []

    def get(self, article_id: str) -> StoredArticle | None:
        self.plain_get_calls.append(article_id)
        return super().get(article_id)

    def get_detail(self, article_id: str, *, lang: str | None = None) -> StoredArticle | None:
        self.get_detail_calls.append((article_id, lang))
        return super().get_detail(article_id, lang=lang)

    def get_many(self, article_ids: list[str]) -> dict[str, StoredArticle]:
        self.plain_get_many_calls.append(tuple(article_ids))
        return super().get_many(article_ids)

    def get_many_detail(
        self, article_ids: list[str], *, lang: str | None = None
    ) -> dict[str, StoredArticle]:
        self.get_many_detail_calls.append((tuple(article_ids), lang))
        return super().get_many_detail(article_ids, lang=lang)


def _seed(store: InMemoryArticleStore, article_id: str, *, draft: bool = False) -> None:
    store.insert(
        StoredArticle(
            article_id=article_id,
            service_id="svc",
            title="T",
            summary="S",
            body="B",
            published_at_epoch=1,
            draft=draft,
        )
    )


def test_get_article_prefers_get_detail_and_forwards_lang() -> None:
    """NewsService.get_article routes through _fetch_detail, which must call store.get_detail(article_id, lang=...) -- never the full-map store.get()."""
    store = _SpyStore()
    _seed(store, "id-1")
    detail = NewsService(store=store).get_article("id-1", lang="de")
    assert detail is not None
    assert store.get_detail_calls == [("id-1", "de")]
    assert store.plain_get_calls == []


def test_get_article_forwards_none_lang_when_no_locale_requested() -> None:
    """No lang requested (English/default) still routes through get_detail, with lang=None -- the store decides not to fetch translations at all."""
    store = _SpyStore()
    _seed(store, "id-1")
    NewsService(store=store).get_article("id-1")
    assert store.get_detail_calls == [("id-1", None)]


def test_get_article_ignoring_draft_gate_also_uses_get_detail() -> None:
    """get_article_ignoring_draft_gate shares _fetch_detail with get_article, so it gets the same lighter read."""
    store = _SpyStore()
    _seed(store, "id-draft", draft=True)
    result = NewsService(store=store).get_article_ignoring_draft_gate("id-draft", "ja")
    assert result is not None
    assert store.get_detail_calls == [("id-draft", "ja")]


def test_get_articles_prefers_get_many_detail_and_forwards_lang() -> None:
    """The bulk get_articles path routes through store.get_many_detail(ids, lang=...) -- never the full-map store.get_many()."""
    store = _SpyStore()
    _seed(store, "id-1")
    _seed(store, "id-2")
    items = NewsService(store=store).get_articles(["id-1", "id-2"], lang="ru")
    assert set(items.keys()) == {"id-1", "id-2"}
    # NewsService.get_articles itself must call get_many_detail, not
    # get_many -- InMemoryArticleStore.get_many_detail's own delegation to
    # self.get_many() underneath is an implementation detail of the fake
    # store, not something under test here.
    assert store.get_many_detail_calls == [(("id-1", "id-2"), "ru")]
