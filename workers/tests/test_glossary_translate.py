"""Glossary term translation: the LLM call for a short term+definition pair, and the Celery task that reads/writes it (mirrors translate_article_task's shape)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.statements import GlossaryStmts
from app.modules.newspaper.glossary_translate import translate_glossary_term
from app.modules.newspaper.tasks.publish_tasks import translate_glossary_term_task


def _client(response: dict[str, str]) -> MagicMock:
    client = MagicMock()
    client.chat_json_object.return_value = response
    return client


def test_translate_glossary_term_returns_translated_pair() -> None:
    """The model's term/definition JSON passes straight through."""
    client = _client({"term": "Staking liquide", "definition": "Definition en francais."})

    result = translate_glossary_term(
        term="Liquid staking", definition="Staking that stays tradeable.", target_language="fr", client=client
    )

    assert result == {"term": "Staking liquide", "definition": "Definition en francais."}


def test_translate_glossary_term_falls_back_to_source_on_empty_response() -> None:
    """A malformed/empty model response must not silently produce a blank translation."""
    client = _client({"term": "", "definition": ""})

    result = translate_glossary_term(
        term="Liquid staking", definition="Staking that stays tradeable.", target_language="fr", client=client
    )

    assert result == {"term": "Liquid staking", "definition": "Staking that stays tradeable."}


def _row(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "slug": "liquid-staking",
        "term": "Liquid staking",
        "definition": "Staking that stays tradeable.",
        "translations": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_task_translates_and_writes_when_language_missing(
    monkeypatch: pytest.MonkeyPatch, fake_cassandra_session: MagicMock
) -> None:
    """A fresh (slug, lang) pair gets translated and merge-written as a JSON blob."""
    fake_cassandra_session.execute.return_value.one.return_value = _row()
    monkeypatch.setattr(
        "app.modules.newspaper.glossary_translate.translate_glossary_term",
        lambda **_kw: {"term": "Staking liquide", "definition": "Def FR."},
    )

    result = translate_glossary_term_task("liquid-staking", "fr")

    assert result == {"status": "ok", "slug": "liquid-staking", "lang": "fr"}
    write_stmt, write_params = fake_cassandra_session.execute.call_args_list[-1][0]
    assert write_stmt is GlossaryStmts.UPDATE_TRANSLATIONS
    translations, slug = write_params
    assert slug == "liquid-staking"
    assert json.loads(translations["fr"]) == {"term": "Staking liquide", "definition": "Def FR."}


def test_task_skips_when_language_already_translated(fake_cassandra_session: MagicMock) -> None:
    """Re-enqueueing a language that's already stored is a cheap no-op, not a re-translate."""
    fake_cassandra_session.execute.return_value.one.return_value = _row(
        translations={"fr": json.dumps({"term": "x", "definition": "y"})}
    )

    result = translate_glossary_term_task("liquid-staking", "fr")

    assert result == {"status": "skipped", "reason": "already_translated", "lang": "fr"}
    fake_cassandra_session.execute.assert_called_once()  # only the read, no write


def test_task_errors_cleanly_when_term_not_found(fake_cassandra_session: MagicMock) -> None:
    """A slug that no longer exists (deleted between enqueue and run) errors, not raises."""
    fake_cassandra_session.execute.return_value.one.return_value = None

    result = translate_glossary_term_task("does-not-exist", "fr")

    assert result == {"status": "error", "reason": "term_not_found"}


def test_task_fails_open_on_exception(fake_cassandra_session: MagicMock) -> None:
    """A Cassandra outage is caught and reported, never raised out of the Celery task."""
    fake_cassandra_session.execute.side_effect = RuntimeError("cassandra down")

    result = translate_glossary_term_task("liquid-staking", "fr")

    assert result["status"] == "error"
