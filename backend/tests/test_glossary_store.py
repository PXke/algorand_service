"""Glossary store: CRUD, translation resolution, and the published-only filter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.modules.glossary import store


class _FakeResult:
    """Minimal stand-in for a Cassandra ResultSet: .one() and .was_applied."""

    def __init__(self, one: object = None, *, applied: bool = True) -> None:
        self._one = one
        self.was_applied = applied

    def one(self) -> object:
        return self._one


class _FakeSession:
    """Captures execute() calls; returns canned .one() results in call order, or a fixed list for a bare scan."""

    def __init__(self, *, one_results: list[object] | None = None, rows: list[object] | None = None) -> None:
        self._one_results = list(one_results or [])
        self._rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, stmt: str, params: tuple = ()) -> object:
        self.calls.append((stmt, params))
        if self._rows is not None:
            return self._rows
        one = self._one_results.pop(0) if self._one_results else None
        return _FakeResult(one)

    def prepare(self, cql: str) -> str:
        return cql


def _row(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "slug": "liquid-staking",
        "term": "Liquid staking",
        "definition": "Staking that keeps the staked asset tradeable via a receipt token.",
        "aliases": ["liquid governance"],
        "status": "published",
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 1, tzinfo=UTC),
        "created_by": "admin",
        "translations": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_get_term_resolves_english_by_default() -> None:
    """No lang given -- returns the stored English term/definition/aliases."""
    fake = _FakeSession(one_results=[_row()])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        term = store.get_term("liquid-staking")
    assert term is not None
    assert term.term == "Liquid staking"
    assert term.aliases == ("liquid governance",)


def test_get_term_resolves_translation_when_present() -> None:
    """A stored translation for the requested lang overrides the English fields."""
    translated_row = _row(
        translations={"fr": json.dumps({"term": "Staking liquide", "definition": "Definition en francais."})}
    )
    fake = _FakeSession(one_results=[translated_row])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        term = store.get_term("liquid-staking", lang="fr")
    assert term is not None
    assert term.term == "Staking liquide"
    assert term.definition == "Definition en francais."


def test_get_term_falls_back_to_english_when_translation_missing() -> None:
    """No stored translation for the requested lang -- English survives untouched."""
    fake = _FakeSession(one_results=[_row(translations={})])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        term = store.get_term("liquid-staking", lang="fr")
    assert term is not None
    assert term.term == "Liquid staking"


def test_get_term_returns_none_for_unknown_slug() -> None:
    """No row for this slug -- None, not an exception."""
    fake = _FakeSession(one_results=[None])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        assert store.get_term("does-not-exist") is None


def test_list_terms_filters_to_published_only() -> None:
    """published_only=True drops draft entries from the full scan."""
    rows = [_row(slug="a", status="published"), _row(slug="b", status="draft")]
    fake = _FakeSession(rows=rows)
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        published = store.list_terms(published_only=True)
    assert [t.slug for t in published] == ["a"]


def test_list_terms_sorted_case_insensitively_by_term() -> None:
    """Listing sorts by term text, not slug or insertion order."""
    rows = [_row(slug="b-term", term="beta"), _row(slug="a-term", term="Alpha")]
    fake = _FakeSession(rows=rows)
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        terms = store.list_terms()
    assert [t.term for t in terms] == ["Alpha", "beta"]


def test_upsert_term_preserves_created_at_on_update() -> None:
    """Updating an existing entry keeps its ORIGINAL created_at, not now()."""
    original_created = datetime(2026, 1, 1, tzinfo=UTC)
    existing = _row(created_at=original_created)
    after_write = _row(created_at=original_created)
    # Three .execute() calls happen: the existence check, the UPSERT itself
    # (its result is unused but still consumes a slot), and upsert_term's own
    # get_term() re-read at the end.
    fake = _FakeSession(one_results=[existing, None, after_write])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        store.upsert_term(
            slug="liquid-staking",
            term="Liquid staking",
            definition="Updated definition.",
            status="published",
        )
    # calls[0] is the GET existence check, calls[1] is the UPSERT itself.
    upsert_params = fake.calls[1][1]
    assert upsert_params[5] == original_created  # created_at param


def test_delete_term_returns_false_for_unknown_slug() -> None:
    """Nothing to delete -- False, and no DELETE statement executed."""
    fake = _FakeSession(one_results=[None])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        assert store.delete_term("does-not-exist") is False
    assert len(fake.calls) == 1  # only the existence check, no DELETE


def test_delete_term_returns_true_and_deletes_when_found() -> None:
    """An existing entry is deleted -- True, and a second (DELETE) call was made."""
    fake = _FakeSession(one_results=[_row()])
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        assert store.delete_term("liquid-staking") is True
    assert len(fake.calls) == 2  # existence check, then DELETE


def test_update_term_translations_merges_not_replaces() -> None:
    """A successful merge-write reports True."""
    fake = _FakeSession()
    fake.execute = lambda *_a, **_kw: _FakeResult(applied=True)  # type: ignore[method-assign]
    with patch("app.core.cassandra.get_cassandra_session", return_value=fake):
        assert store.update_term_translations("liquid-staking", {"fr": "{}"}) is True


def test_update_term_translations_noop_on_empty_input() -> None:
    """No translations to write -- never touches Cassandra at all."""

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("must not touch Cassandra with no translations to write")

    with patch("app.core.cassandra.get_cassandra_session", _boom):
        assert store.update_term_translations("liquid-staking", {}) is False


def test_enqueue_glossary_term_translations_fires_one_task_per_language() -> None:
    """One send_task per ARTICLE_TRANSLATION_LANGS entry, same task-name dispatch as articles."""
    from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS

    calls: list[tuple] = []
    fake_celery = SimpleNamespace(send_task=lambda *a, **kw: calls.append((a, kw)))
    with patch("celery.Celery", return_value=fake_celery):
        store.enqueue_glossary_term_translations("liquid-staking")
    assert len(calls) == len(ARTICLE_TRANSLATION_LANGS)
    for args, kwargs in calls:
        assert args[0] == "app.tasks.newspaper.translate_glossary_term"
        assert kwargs["args"][0] == "liquid-staking"
        assert kwargs["queue"] == "translate"
    langs_dispatched = {kwargs["args"][1] for _args, kwargs in calls}
    assert langs_dispatched == set(ARTICLE_TRANSLATION_LANGS)


def test_enqueue_glossary_term_translations_fails_open_on_broker_error() -> None:
    """A broker/Celery construction failure must never raise out to the caller."""
    with patch("celery.Celery", side_effect=RuntimeError("no broker")):
        store.enqueue_glossary_term_translations("liquid-staking")  # must not raise
