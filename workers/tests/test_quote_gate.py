"""Quotation-integrity gate (2026-07-16): the RandGallery shutdown article
attributed an INVENTED phrase to the Goanna Council in quotation marks —
"stack of legacy systems layered on top of one another" existed nowhere in
the research trace or the supplied announcement (the announcement didn't even
contain the word "legacy"). Quotation marks are a verbatim-transcription
claim; an unverifiable quote becomes a paraphrase (marks dropped, words
kept), never a lost sentence.
"""

from __future__ import annotations

import pytest

from app.modules.newspaper.quote_gate import unquote_ungrounded_quotes

_ANNOUNCEMENT = (
    "On behalf of The Goanna Council. We'll begin winding up RandGallery over "
    "the coming weeks. Most importantly: all NFTs are SAFU. Before we wind "
    "down, we'll ensure every NFT is returned to its rightful owner."
)

_TRACE = [
    {
        "tool": "fetch_url",
        "arguments": {"url": "https://www.randgallery.com/"},
        "result": {"text": "Rand Gallery - The Home of Algorand NFTs"},
    }
]


@pytest.fixture(autouse=True)
def _gate_enabled(monkeypatch):
    monkeypatch.setattr("app.core.config.QUOTE_GATE_ENABLED", True, raising=False)


def test_invented_quote_is_dequoted_but_words_survive() -> None:
    payload = {
        "body": 'The codebase, described as a "stack of legacy systems layered '
        'on top of one another," lacked documentation.'
    }
    out = unquote_ungrounded_quotes(payload, _TRACE, extra_texts=[_ANNOUNCEMENT])
    assert out["body"] == (
        "The codebase, described as a stack of legacy systems layered "
        "on top of one another, lacked documentation."
    )
    assert out["_quotes_unquoted"] == [
        "stack of legacy systems layered on top of one another,"
    ]


def test_verbatim_quote_from_compose_input_is_kept() -> None:
    # The announcement arrived via the editorial brief (compose input, not the
    # trace) — quoting it verbatim is legitimate journalism and must survive.
    payload = {
        "body": 'The Council promised every NFT is "returned to its rightful owner".'
    }
    out = unquote_ungrounded_quotes(payload, _TRACE, extra_texts=[_ANNOUNCEMENT])
    assert '"returned to its rightful owner"' in out["body"]
    assert "_quotes_unquoted" not in out


def test_verbatim_quote_from_trace_is_kept() -> None:
    payload = {"body": 'Its tagline reads "The Home of Algorand NFTs" today.'}
    out = unquote_ungrounded_quotes(payload, _TRACE, extra_texts=[])
    assert '"The Home of Algorand NFTs"' in out["body"]


def test_matching_is_case_and_punctuation_insensitive() -> None:
    # Curly quotes, commas and capitalization must not defeat the match.
    payload = {"body": "They said “all NFTs are SAFU. before we WIND down” yesterday."}
    out = unquote_ungrounded_quotes(payload, [], extra_texts=[_ANNOUNCEMENT])
    assert "“all NFTs are SAFU. before we WIND down”" in out["body"]


def test_short_quoted_fragments_are_left_alone() -> None:
    # Scare quotes / names carry little verbatim claim; 1-3 words pass.
    payload = {"body": 'The "messy" codebase and the "Goanna Council" remained.'}
    out = unquote_ungrounded_quotes(payload, [], extra_texts=[])
    assert out["body"] == 'The "messy" codebase and the "Goanna Council" remained.'


def test_curly_quoted_invention_also_dequoted() -> None:
    payload = {"body": "He called it “a revolutionary paradigm shift for everyone involved”."}
    out = unquote_ungrounded_quotes(payload, _TRACE, extra_texts=[_ANNOUNCEMENT])
    assert out["body"] == "He called it a revolutionary paradigm shift for everyone involved."


def test_gate_disabled_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.QUOTE_GATE_ENABLED", False, raising=False)
    body = 'X said "totally invented words that ground nowhere at all".'
    out = unquote_ungrounded_quotes({"body": body}, [], extra_texts=[])
    assert out["body"] == body
