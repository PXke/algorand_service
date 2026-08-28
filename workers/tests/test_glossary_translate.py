"""Glossary term translation: the LLM call for a short term+definition pair, and the Celery task that reads/writes it (mirrors translate_article_task's shape)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.statements import GlossaryStmts, ToolInsightStmts
from app.modules.newspaper.glossary_translate import translate_glossary_term
from app.modules.newspaper.tasks.publish_tasks import translate_glossary_term_task


def _client(response: dict[str, str]) -> MagicMock:
    client = MagicMock()
    client.chat_json_object.return_value = response
    client.model = "deepseek-v4-flash"
    client.usage_totals.return_value = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }
    return client


def _insert_compose_session_call(session: MagicMock) -> tuple:
    """The one execute() call (among the fake session's calls) that wrote compose_sessions -- glossary translation's own usage-accounting row (2026-08-28 audit; see glossary_translate._record_glossary_translate_session)."""
    return next(
        c.args[1]
        for c in session.execute.call_args_list
        if c.args[0] is ToolInsightStmts.INSERT_COMPOSE_SESSION
    )


def test_translate_glossary_term_returns_translated_pair() -> None:
    """The model's term/definition JSON passes straight through."""
    client = _client({"term": "Staking liquide", "definition": "Definition en francais."})

    result = translate_glossary_term(
        term="Liquid staking",
        definition="Staking that stays tradeable.",
        target_language="fr",
        client=client,
    )

    assert result == {"term": "Staking liquide", "definition": "Definition en francais."}


def test_translate_glossary_term_falls_back_to_source_on_empty_response() -> None:
    """A malformed/empty model response must not silently produce a blank translation."""
    client = _client({"term": "", "definition": ""})

    result = translate_glossary_term(
        term="Liquid staking",
        definition="Staking that stays tradeable.",
        target_language="fr",
        client=client,
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


# --------------------------------------------------------------------------- #
# Usage accounting (2026-08-28 audit): translate_glossary_term runs on its own
# ephemeral translate-tier client, entirely OUTSIDE any article compose
# session -- nothing else ever accounted for its real spend. It now records
# its own compose_sessions row via the SAME mechanism (SessionRegisterCassandra
# -> tool_insights_store.record_compose_session) the article compose path
# already writes to, service_id-prefixed so it's distinguishable from an
# article compose in the admin Sessions tab.
# --------------------------------------------------------------------------- #


def test_translate_glossary_term_records_its_own_usage_session(
    fake_cassandra_session: MagicMock,
) -> None:
    """A successful translation writes a compose_sessions row carrying the client's real usage_totals()."""
    client = _client({"term": "Staking liquide", "definition": "Definition en francais."})
    client.usage_totals.return_value = {
        "prompt_tokens": 40,
        "completion_tokens": 15,
        "total_tokens": 55,
        "cached_tokens": 5,
    }

    translate_glossary_term(
        term="Liquid staking",
        definition="Staking that stays tradeable.",
        target_language="fr",
        client=client,
    )

    params = _insert_compose_session_call(fake_cassandra_session)
    # (bucket, created_at, session_id, service_id, source_url, model, status,
    #  rounds, tool_calls, duration_ms, messages, final_output,
    #  prompt_tokens, completion_tokens, total_tokens, cached_tokens)
    (
        _bucket,
        _created_at,
        _session_id,
        service_id,
        _source_url,
        model,
        status,
        _rounds,
        _tool_calls,
        _duration_ms,
        _messages,
        _final_output,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        cached_tokens,
    ) = params
    assert service_id.startswith("glossary_translate:")
    assert "Liquid staking" in service_id
    assert model == "deepseek-v4-flash"
    assert status == "ok"
    assert (prompt_tokens, completion_tokens, total_tokens, cached_tokens) == (40, 15, 55, 5)


def test_translate_glossary_term_records_error_status_and_still_raises(
    fake_cassandra_session: MagicMock,
) -> None:
    """A translation failure must still propagate to the caller (translate_glossary_term_task's own fail-open try/except handles it) -- recording the session must never swallow the real exception."""
    client = MagicMock()
    client.model = "deepseek-v4-flash"
    client.chat_json_object.side_effect = RuntimeError("upstream boom")
    client.usage_totals.return_value = {
        "prompt_tokens": 10,
        "completion_tokens": 0,
        "total_tokens": 10,
        "cached_tokens": 0,
    }

    with pytest.raises(RuntimeError, match="upstream boom"):
        translate_glossary_term(
            term="Liquid staking",
            definition="Staking that stays tradeable.",
            target_language="fr",
            client=client,
        )

    params = _insert_compose_session_call(fake_cassandra_session)
    status = params[6]
    prompt_tokens = params[12]
    assert status == "error"
    assert prompt_tokens == 10  # whatever the client had spent before failing is still recorded


def test_translate_glossary_term_session_recording_failure_does_not_break_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken usage-accounting write (e.g. Cassandra down) is best-effort -- it must never turn a SUCCESSFUL translation into a failure."""
    client = _client({"term": "Staking liquide", "definition": "Definition en francais."})

    monkeypatch.setattr(
        "app.modules.ai.session_register.SessionRegisterCassandra",
        lambda: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )

    result = translate_glossary_term(
        term="Liquid staking",
        definition="Staking that stays tradeable.",
        target_language="fr",
        client=client,
    )

    assert result == {"term": "Staking liquide", "definition": "Definition en francais."}
