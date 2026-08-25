"""_apply_post_compose_gates must reuse the caller's client for glossary suggestion.

A fresh internal client fetch bypasses whatever a caller (or its test)
already mocked and was capable of firing a REAL, unmocked network call
(found 2026-08-03: it hung test_mistral_cost_controls.py for minutes, not
seconds, before this fix).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.ai.llm_compose import _apply_post_compose_gates


def _payload() -> dict:
    return {"title": "T", "body": "Body mentioning liquid staking."}


def test_glossary_gate_uses_the_client_it_was_given(fake_cassandra_session: MagicMock) -> None:
    """A glossary_client is passed straight through to the extraction call, no independent client fetch."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    client = MagicMock()
    client.chat_json_object.return_value = {"terms": [{"term": "Liquid staking", "definition": "x."}]}

    _apply_post_compose_gates(
        _payload(), [], user="u", research_user=None, glossary_client=client
    )

    client.chat_json_object.assert_called_once()


def test_glossary_gate_is_skipped_without_a_client(fake_cassandra_session: MagicMock) -> None:
    """No client given (legacy path) -- the gate is a no-op, not an internal client fetch."""
    result = _apply_post_compose_gates(_payload(), [], user="u", research_user=None)

    assert result["title"] == "T"
    fake_cassandra_session.execute.assert_not_called()
