"""update_article must clear + re-enqueue translations when content actually changes -- an admin correction previously left every translation exactly as it was BEFORE the fix, silently wrong in every non-English locale (found live 2026-08)."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore
from app.modules.news.stores.base import StoredArticle


def _article(**overrides: Any) -> StoredArticle:  # noqa: ANN401
    base = {
        "article_id": "11111111-1111-1111-1111-111111111111",
        "service_id": "svc",
        "title": "Original title",
        "summary": "Original summary",
        "body": "Original body",
        "published_at_epoch": 1000,
        "translations": {"fr": "{}", "es": "{}"},
        "slug": "original-title",
    }
    base.update(overrides)
    return StoredArticle(**base)


def _store_with_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: StoredArticle,
    updated: StoredArticle,
    typesense_calls: list[dict] | None = None,
) -> tuple[AdminCassandraStore, list[str]]:
    store = AdminCassandraStore()
    calls: list[str] = []

    get_calls = iter([current, updated])
    monkeypatch.setattr(store, "get_article", lambda _id: next(get_calls))
    monkeypatch.setattr(store, "_save_version_snapshot", lambda *_a, **_kw: None)
    monkeypatch.setattr(store, "_write_article", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        AdminCassandraStore,
        "_clear_and_reenqueue_translations",
        staticmethod(lambda article_id: calls.append(article_id)),
    )
    monkeypatch.setattr("app.modules.seo.indexnow.ping_article", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "app.core.typesense_client.upsert_article_document",
        lambda **kw: (typesense_calls.append(kw) if typesense_calls is not None else None),
    )
    return store, calls


def test_body_change_clears_and_reenqueues_translations(monkeypatch: pytest.MonkeyPatch) -> None:
    """A body edit is a real content change and must clear/re-enqueue translations."""
    current = _article()
    updated = _article(body="Corrected body")
    store, calls = _store_with_fakes(monkeypatch, current=current, updated=updated)

    store.update_article(current.article_id, body="Corrected body")

    assert calls == [current.article_id]


def test_update_article_reindexes_typesense_with_the_new_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An admin edit must re-index Typesense with the corrected title/summary/body immediately, not wait on the once-daily reindex_articles safety net."""
    current = _article()
    updated = _article(title="Corrected title", body="Corrected body")
    typesense_calls: list[dict] = []
    store, _calls = _store_with_fakes(
        monkeypatch, current=current, updated=updated, typesense_calls=typesense_calls
    )

    store.update_article(current.article_id, title="Corrected title", body="Corrected body")

    assert len(typesense_calls) == 1
    assert typesense_calls[0] == {
        "article_id": updated.article_id,
        "title": "Corrected title",
        "summary": updated.summary,
        "body": "Corrected body",
        "service_id": updated.service_id,
        "published_at_epoch": updated.published_at_epoch,
        "translations": updated.translations,
        "slug": updated.slug,
    }


def test_title_only_change_also_clears_translations(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title-only edit is still a real content change and must clear/re-enqueue translations."""
    current = _article()
    updated = _article(title="Corrected title")
    store, calls = _store_with_fakes(monkeypatch, current=current, updated=updated)

    store.update_article(current.article_id, title="Corrected title")

    assert calls == [current.article_id]


def test_no_actual_change_does_not_touch_translations(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling update_article with the SAME title/summary/body as already stored must not wipe good translations for nothing."""
    current = _article()
    store, calls = _store_with_fakes(monkeypatch, current=current, updated=current)

    store.update_article(current.article_id, title=current.title)

    assert calls == []


def test_no_stored_translations_skips_the_clear_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """An article with nothing translated yet has nothing to clear -- must not fire a pointless re-enqueue."""
    current = _article(translations=None)
    updated = _article(translations=None, body="Corrected body")
    store, calls = _store_with_fakes(monkeypatch, current=current, updated=updated)

    store.update_article(current.article_id, body="Corrected body")

    assert calls == []


def test_missing_article_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """update_article on an id that no longer resolves to a real article must not attempt to clear translations."""
    store = AdminCassandraStore()
    monkeypatch.setattr(store, "get_article", lambda _id: None)
    calls: list[str] = []
    monkeypatch.setattr(
        AdminCassandraStore,
        "_clear_and_reenqueue_translations",
        staticmethod(lambda article_id: calls.append(article_id)),
    )

    result = store.update_article("missing-id", body="whatever")

    assert result is None
    assert calls == []


class _FakeCassandraSession:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, Any]] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, stmt: Any, params: Any = ()) -> None:  # noqa: ANN401
        self.executed.append((stmt, params))


def test_clear_and_reenqueue_sends_the_batch_task_for_every_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real (non-mocked) _clear_and_reenqueue_translations must DELETE the map and fan out to translate_article_batch -- not the legacy per-language shim, which bypasses local/DeepSeek routing entirely."""
    import app.core.cassandra as c

    fake_session = _FakeCassandraSession()
    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake_session)
    c.prepare_cached.cache_clear()
    sent: dict[str, Any] = {}

    class _FakeCelery:
        def __init__(self, broker: str) -> None:
            pass

        def send_task(self, name: str, *, args: list, queue: str) -> None:
            sent["name"] = name
            sent["args"] = args
            sent["queue"] = queue

    monkeypatch.setattr("celery.Celery", _FakeCelery)

    AdminCassandraStore._clear_and_reenqueue_translations("11111111-1111-1111-1111-111111111111")

    # 1 `articles`-table lookup (best-effort, swallowed here since the fake
    # session's execute() doesn't support .one() -- the subsequent
    # CLEAR_TRANSLATIONS write never runs).
    assert len(fake_session.executed) == 1
    assert sent["name"] == "app.tasks.newspaper.translate_article_batch"
    assert sent["args"][0] == "11111111-1111-1111-1111-111111111111"
    assert len(sent["args"][1]) >= 6  # every configured language, not one
    assert sent["queue"] == "translate"


def test_clear_and_reenqueue_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cassandra/Celery failure here must never raise into update_article -- an admin correction saving successfully must not depend on the translation fan-out succeeding."""
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    AdminCassandraStore._clear_and_reenqueue_translations("11111111-1111-1111-1111-111111111111")
