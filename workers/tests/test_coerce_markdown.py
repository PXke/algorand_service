"""Body coercion: a model that emits `body` as nested JSON (object/list) instead
of a markdown string must be flattened back to real markdown, not str(dict)'d."""

from app.modules.ai.mistral_compose import _coerce_markdown


def test_plain_string_passthrough() -> None:
    assert _coerce_markdown("## Hi\n\ntext") == "## Hi\n\ntext"


def test_dict_body_becomes_markdown_sections() -> None:
    body = {
        "## Market snapshot": "\n| A | B |\n",
        "This week": "\n### Item\nblurb\n",
    }
    out = _coerce_markdown(body)
    # Keys that already start with '#' are kept; bare keys are promoted to h2.
    assert "## Market snapshot" in out
    assert "## This week" in out
    assert "| A | B |" in out
    # No Python dict repr leaked through.
    assert "{" not in out and "': '" not in out


def test_list_body_joined() -> None:
    assert _coerce_markdown(["one", "two"]) == "one\n\ntwo"


def test_none_is_empty() -> None:
    assert _coerce_markdown(None) == ""
