"""Post-hoc glossary extraction gate.

Unlike the suggest_glossary_term tool (only callable during Stage 1, before
the article exists), this gate classifies the FINISHED body and queues the
same draft rows -- see glossary_suggest_gate.py's docstring for why the tool
path was empirically unreachable (0/62 sessions).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.glossary_suggest_gate import suggest_glossary_terms


def _client(terms: list[dict[str, str]]) -> MagicMock:
    client = MagicMock()
    client.chat_json_object.return_value = {"terms": terms}
    return client


def test_queues_a_draft_row_for_each_extracted_term(fake_cassandra_session: MagicMock) -> None:
    """A term+definition pair from the classifier becomes a draft glossary row."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    client = _client([{"term": "Liquid staking", "definition": "Staking that stays tradeable."}])

    payload = {"title": "T", "body": "Some article body about liquid staking."}
    result = suggest_glossary_terms(payload, client=client, service_id="svc-1")

    assert result is payload  # never mutates the article fields
    assert fake_cassandra_session.execute.call_count == 1


def test_skips_cassandra_entirely_when_classifier_returns_no_terms(
    fake_cassandra_session: MagicMock,
) -> None:
    """Most articles have nothing to flag -- empty terms list is the expected common case."""
    suggest_glossary_terms({"title": "T", "body": "Plain body."}, client=_client([]), service_id="svc-1")

    fake_cassandra_session.execute.assert_not_called()


def test_caps_at_max_terms_even_if_the_model_returns_more(fake_cassandra_session: MagicMock) -> None:
    """A misbehaving classifier response can't queue an unbounded number of drafts."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    many_terms = [{"term": f"Term {i}", "definition": f"Definition {i}."} for i in range(10)]

    suggest_glossary_terms({"title": "T", "body": "Body."}, client=_client(many_terms), service_id="svc-1")

    assert fake_cassandra_session.execute.call_count == 3


def test_disabled_by_config_never_calls_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """GLOSSARY_SUGGEST_GATE_ENABLED=False is a full no-op, no LLM call at all."""
    import app.core.config as config

    monkeypatch.setattr(config, "GLOSSARY_SUGGEST_GATE_ENABLED", False)
    client = MagicMock()

    suggest_glossary_terms({"title": "T", "body": "Body."}, client=client)

    client.chat_json_object.assert_not_called()


def test_skips_when_body_is_missing(fake_cassandra_session: MagicMock) -> None:
    """No body means nothing to classify -- must not raise or touch Cassandra."""
    suggest_glossary_terms({"title": "T", "body": ""}, client=MagicMock())
    fake_cassandra_session.execute.assert_not_called()


def test_fails_open_on_classifier_error(fake_cassandra_session: MagicMock) -> None:
    """A broken/rate-limited LLM call must never affect the already-composed article."""
    client = MagicMock()
    client.chat_json_object.side_effect = RuntimeError("mistral down")

    payload = {"title": "T", "body": "Body."}
    result = suggest_glossary_terms(payload, client=client)

    assert result is payload
    fake_cassandra_session.execute.assert_not_called()


def test_ignores_malformed_term_entries(fake_cassandra_session: MagicMock) -> None:
    """A term missing its definition (or a non-dict entry) is dropped, not queued blank."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    client = _client([{"term": "No definition"}, "not a dict", {"term": "", "definition": "x"}])

    suggest_glossary_terms({"title": "T", "body": "Body."}, client=client)

    fake_cassandra_session.execute.assert_not_called()
