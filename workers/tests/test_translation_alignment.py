"""Translations must stay block-aligned to their English source.

Translation is the only content lane with no quality gate: it fires at publish,
writes straight to the article, and nothing ever checked the result. An audit of
the 660 stored translations (2026-07-29) found 18.5% whose paragraph count
differed from the source, including a Persian article that collapsed 42
paragraphs into 9 — three quarters of the piece silently gone, live and indexed.

Block alignment is the cheapest gate that works on the six languages nobody on
the team can read: exactly one output block per source block, checked at parse
time, retried once, and refused rather than stored when it cannot be fixed.
"""

from types import SimpleNamespace

import pytest

from app.modules.ai import mistral_compose as mc
from app.modules.ai.mistral_compose import TranslationAlignmentError, split_markdown_blocks

_BODY = """## Heading

Intro paragraph with a [link](https://x.io) and 10.7M in it.

- item one
- item two

| Col | Val |
|-----|-----|
| a   | 1   |

Closing paragraph."""


def _client(monkeypatch: pytest.MonkeyPatch, *payloads: dict) -> list[list[dict]]:
    """Install a fake Mistral client returning `payloads` in order; returns the calls it saw."""
    seen: list[list[dict]] = []
    queue = list(payloads)

    def _chat_json_object(messages: list[dict], *_a: object, **_kw: object) -> dict:
        seen.append(list(messages))
        return queue.pop(0) if queue else {}

    monkeypatch.setattr(
        mc,
        "get_mistral_client",
        lambda **_kw: SimpleNamespace(chat_json_object=_chat_json_object),
    )
    return seen


def test_split_keeps_tables_and_fences_whole() -> None:
    """Splits on blank lines but never inside a fenced code block, and keeps a table as one block."""
    blocks = split_markdown_blocks(_BODY)
    assert len(blocks) == 5
    assert blocks[0] == "## Heading"
    assert blocks[2] == "- item one\n- item two"
    assert blocks[3].count("|") > 6  # table intact, not split per row

    fenced = "Intro.\n\n```python\nx = 1\n\ny = 2\n```\n\nOutro."
    fb = split_markdown_blocks(fenced)
    assert len(fb) == 3
    assert fb[1].count("```") == 2  # the blank line INSIDE the fence did not split it


def test_empty_body_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns the source untouched (and calls no model) when there is nothing to translate."""
    seen = _client(monkeypatch)
    out = mc.translate_article_mistral(
        english_title="T", english_summary="S", english_body="   ", target_language="fr"
    )
    assert out["body"] == "   "
    assert seen == []


def test_aligned_translation_is_rejoined_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejoins exactly one translated block per source block, preserving order."""
    n = len(split_markdown_blocks(_BODY))
    _client(
        monkeypatch,
        {"title": "T", "summary": "S", "blocks": [f"bloc {i}" for i in range(n)]},
    )
    out = mc.translate_article_mistral(
        english_title="T", english_summary="S", english_body=_BODY, target_language="fr"
    )
    assert split_markdown_blocks(out["body"]) == [f"bloc {i}" for i in range(n)]


def test_misalignment_triggers_one_corrective_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short block list is retried once, told the counts, and the corrected result is kept."""
    n = len(split_markdown_blocks(_BODY))
    seen = _client(
        monkeypatch,
        {"title": "T", "summary": "S", "blocks": ["merged everything"]},  # too few
        {"title": "T", "summary": "S", "blocks": [f"b{i}" for i in range(n)]},  # corrected
    )
    out = mc.translate_article_mistral(
        english_title="T", english_summary="S", english_body=_BODY, target_language="fr"
    )
    assert len(seen) == 2
    assert len(split_markdown_blocks(out["body"])) == n
    # The retry must name both counts, or the model just re-emits the same shape.
    correction = seen[1][-1]["content"]
    assert "1 blocks" in correction
    assert f"{n} blocks" in correction


def test_unfixable_misalignment_raises_instead_of_storing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuses to return a misaligned translation — a dropped-content body must never reach the store."""
    _client(
        monkeypatch,
        {"title": "T", "summary": "S", "blocks": ["one"]},
        {"title": "T", "summary": "S", "blocks": ["one", "two"]},
    )
    with pytest.raises(TranslationAlignmentError):
        mc.translate_article_mistral(
            english_title="T", english_summary="S", english_body=_BODY, target_language="ps"
        )


def test_blank_block_is_treated_as_misaligned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A right-sized list with an empty entry is a dropped block wearing the right shape."""
    n = len(split_markdown_blocks(_BODY))
    blocks = [f"b{i}" for i in range(n)]
    blocks[2] = "   "
    _client(
        monkeypatch,
        {"title": "T", "summary": "S", "blocks": blocks},
        {"title": "T", "summary": "S", "blocks": blocks},
    )
    with pytest.raises(TranslationAlignmentError):
        mc.translate_article_mistral(
            english_title="T", english_summary="S", english_body=_BODY, target_language="ar"
        )


def test_prompt_states_the_block_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The block count and the [n] markers are both in the prompt — the model cannot align without them."""
    n = len(split_markdown_blocks(_BODY))
    seen = _client(
        monkeypatch,
        {"title": "T", "summary": "S", "blocks": [f"b{i}" for i in range(n)]},
    )
    mc.translate_article_mistral(
        english_title="T", english_summary="S", english_body=_BODY, target_language="fr"
    )
    system, user = seen[0][0]["content"], seen[0][1]["content"]
    assert f"EXACTLY {n}" in system or f"Return EXACTLY {n}" in system
    assert "[1]" in user
    assert f"[{n}]" in user
