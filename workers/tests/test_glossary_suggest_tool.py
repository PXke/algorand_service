"""suggest_glossary_term writer tool: the writer flags a candidate term, an admin decides -- the handler only ever writes a draft row, gated by the DB's own IF NOT EXISTS so a duplicate slug never overwrites an existing entry."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.core.statements import GlossaryStmts
from app.modules.ai.glossary_suggest_tool import (
    SUGGEST_GLOSSARY_TERM_SCHEMA,
    _make_suggest_glossary_term_handler,
    _slugify,
)


def test_slugify_lowercases_and_hyphenates() -> None:
    """Non-alphanumeric runs collapse to single hyphens, case-folded."""
    assert _slugify("Liquid Staking!") == "liquid-staking"


def test_slugify_strips_leading_and_trailing_hyphens() -> None:
    """Punctuation at either end never leaks into the slug."""
    assert _slugify("  Pure Proof-of-Stake  ") == "pure-proof-of-stake"


def test_handler_writes_a_draft_row(fake_cassandra_session: MagicMock) -> None:
    """A fresh term is inserted with status=draft and a writer:-prefixed created_by."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    handler = _make_suggest_glossary_term_handler({"service_id": "svc-1", "model": "m"})

    result = handler(term="Liquid staking", definition="Staking that keeps the asset tradeable.")

    assert result == {"ok": True, "slug": "liquid-staking", "noted": "Liquid staking"}
    stmt, params = fake_cassandra_session.execute.call_args[0]
    assert stmt is GlossaryStmts.INSERT_SUGGESTED
    slug, term, _definition, aliases, _created_at, _updated_at, created_by = params
    assert slug == "liquid-staking"
    assert term == "Liquid staking"
    assert aliases == []
    assert created_by == "writer:svc-1"


def test_handler_falls_back_to_model_when_no_service_id(fake_cassandra_session: MagicMock) -> None:
    """created_by still gets a writer: prefix even without a service_id in context."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=True)
    handler = _make_suggest_glossary_term_handler({"model": "mistral-large"})

    handler(term="Pure Proof-of-Stake", definition="Algorand's consensus mechanism.")

    _slug, _term, _definition, _aliases, _c, _u, created_by = (
        fake_cassandra_session.execute.call_args[0][1]
    )
    assert created_by == "writer:mistral-large"


def _glossary_row(slug: str, aliases: list[str]) -> MagicMock:
    row = MagicMock()
    row.slug = slug
    row.aliases = aliases
    return row


def test_handler_rejects_a_term_matching_an_existing_alias_exactly(
    fake_cassandra_session: MagicMock,
) -> None:
    """Suggesting 'LST' is rejected when 'Liquid Staking' already exists with 'LST' as an alias -- root-caused 2026-08-07: aliases were invisible to the duplicate check entirely, so this sailed through as a new term."""
    fake_cassandra_session.execute.return_value = [
        _glossary_row("liquid-staking", ["LST", "Liquid staking token"])
    ]
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="LST", definition="A liquid staking derivative token.")

    assert result["ok"] is False
    assert result["already_exists"] is True
    assert result["slug"] == "liquid-staking"  # the CANONICAL slug, not a synthetic alias-slug
    assert "already in the glossary" in result["hint"]


def test_handler_catches_a_near_duplicate_of_an_existing_alias(
    fake_cassandra_session: MagicMock,
) -> None:
    """A reworded near-match of an ALIAS (not the canonical term) is still caught by the fuzzy check."""
    fake_cassandra_session.execute.return_value = [
        _glossary_row("liquid-staking", ["Liquid staking token (LST)"])
    ]
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="Liquid staking token", definition="Something.")

    assert result["ok"] is False
    assert result["already_exists"] is True
    assert result["slug"] == "liquid-staking"
    assert "reworded" in result["hint"]


def test_handler_still_accepts_a_genuinely_new_term_with_unrelated_aliases(
    fake_cassandra_session: MagicMock,
) -> None:
    """An existing term's aliases must not cause unrelated new terms to be falsely rejected."""
    fake_cassandra_session.execute.side_effect = [
        [_glossary_row("liquid-staking", ["LST"])],  # LIST_ALL for the duplicate check
        MagicMock(was_applied=True),  # the INSERT itself
    ]
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="Pure Proof-of-Stake", definition="Algorand's consensus mechanism.")

    assert result == {"ok": True, "slug": "pure-proof-of-stake", "noted": "Pure Proof-of-Stake"}


def test_handler_rejects_a_slug_that_already_exists(fake_cassandra_session: MagicMock) -> None:
    """The DB's IF NOT EXISTS guard failing to apply is surfaced as already_exists, not an error."""
    fake_cassandra_session.execute.return_value = MagicMock(was_applied=False)
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="Liquid staking", definition="Something.")

    assert result["ok"] is False
    assert result["already_exists"] is True
    assert result["slug"] == "liquid-staking"


def test_handler_requires_both_term_and_definition(fake_cassandra_session: MagicMock) -> None:
    """Missing term or definition is rejected before any Cassandra call."""
    handler = _make_suggest_glossary_term_handler({})

    assert handler(term="", definition="x")["ok"] is False
    assert handler(term="x", definition="")["ok"] is False
    fake_cassandra_session.execute.assert_not_called()


def test_handler_rejects_a_term_with_no_usable_slug(fake_cassandra_session: MagicMock) -> None:
    """A term made entirely of punctuation/symbols slugifies to empty and is rejected."""
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="!!!", definition="x")

    assert result["ok"] is False
    fake_cassandra_session.execute.assert_not_called()


def test_handler_fails_open_on_cassandra_error(fake_cassandra_session: MagicMock) -> None:
    """A Cassandra exception is caught -- the article write must never be blocked by this tool."""
    fake_cassandra_session.execute.side_effect = RuntimeError("cassandra down")
    handler = _make_suggest_glossary_term_handler({})

    result = handler(term="Liquid staking", definition="Something.")

    assert result == {"ok": False, "error": "could not record suggestion"}


def test_schema_requires_term_and_definition() -> None:
    """Declares both term and definition as required parameters."""
    props = SUGGEST_GLOSSARY_TERM_SCHEMA["function"]["parameters"]
    assert set(props["required"]) == {"term", "definition"}


def test_registered_in_writer_tool_registry() -> None:
    """Registers suggest_glossary_term among the writer's available tool schemas and handlers."""
    from app.modules.ai.writer_tools import all_tools

    schemas, handlers = all_tools(context={"service_id": "x", "source_url": "x", "model": "m"})
    names = {(s.get("function") or {}).get("name") for s in schemas}
    assert "suggest_glossary_term" in names
    assert "suggest_glossary_term" in handlers
