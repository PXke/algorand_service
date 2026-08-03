"""Deterministic glossary auto-linking: first-occurrence-only, structural skips, never re-linking."""

from __future__ import annotations

import pytest

from app.modules.newspaper.glossary_linker import GlossaryLinkTerm, auto_link_glossary_terms

_LIQUID_STAKING = GlossaryLinkTerm(
    slug="liquid-staking",
    term="liquid staking",
    definition="Staking that keeps the staked asset tradeable via a receipt token.",
    aliases=("mALGO",),
)
_PPOS = GlossaryLinkTerm(
    slug="ppos",
    term="Pure Proof-of-Stake",
    definition="Algorand's consensus mechanism.",
    aliases=("PPoS",),
)


def test_links_first_occurrence_preserving_original_casing() -> None:
    """The FIRST match gets linked, with the source text's own casing kept intact."""
    body = "Liquid staking lets you earn yield without locking funds."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert out == (
        '[Liquid staking](/glossary/liquid-staking '
        '"Staking that keeps the staked asset tradeable via a receipt token.") '
        "lets you earn yield without locking funds."
    )


def test_does_not_relink_later_occurrences_of_the_same_term() -> None:
    """Only the first mention of a term links -- repeating the same definition link is noise."""
    body = "Liquid staking is popular. Many services now offer liquid staking to users."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert out.count("/glossary/liquid-staking") == 1


def test_alias_and_term_share_one_link_budget() -> None:
    """An alias (mALGO) and its canonical term (liquid staking) are the SAME entry -- only the first of either links."""
    body = "mALGO holders benefit from liquid staking rewards."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert out.count("/glossary/liquid-staking") == 1
    assert "[mALGO](/glossary/liquid-staking" in out
    assert "liquid staking rewards" in out  # second mention left plain


def test_skips_headings() -> None:
    """A term appearing in a markdown heading is never linked -- headlines stay plain text."""
    body = "## What is liquid staking?\n\nLiquid staking lets you earn yield."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert "## What is liquid staking?" in out  # heading untouched
    assert out.count("/glossary/liquid-staking") == 1  # only the prose mention links


def test_skips_code_fences() -> None:
    """A term inside a fenced code block is never linked."""
    body = "Liquid staking works like this:\n\n```\nliquid_staking.stake(amount)\n```"
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert "liquid_staking.stake(amount)" in out
    assert out.count("/glossary/liquid-staking") == 1  # only the prose mention


def test_never_overlaps_an_existing_markdown_link() -> None:
    """A term that's already part of a different markdown link is left alone, not double-linked."""
    body = "See [our guide to liquid staking](https://example.com/guide) for details."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert out == body  # no glossary link inserted -- the phrase is inside an existing link


def test_recompose_does_not_stack_a_second_link_for_an_already_linked_term() -> None:
    """A body that already has a glossary link for a term (from a prior auto-link pass) doesn't get a second one on a later mention, even without re-processing the original span."""
    body = (
        '[Liquid staking](/glossary/liquid-staking "Old definition.") is popular. '
        "New services also use liquid staking."
    )
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING,))
    assert out.count("/glossary/liquid-staking") == 1  # no second link added


def test_multiple_distinct_terms_each_get_their_own_link() -> None:
    """Two different glossary terms in the same article each link once, independently."""
    body = "Liquid staking relies on Algorand's Pure Proof-of-Stake consensus."
    out = auto_link_glossary_terms(body, (_LIQUID_STAKING, _PPOS))
    assert "/glossary/liquid-staking" in out
    assert "/glossary/ppos" in out


def test_no_terms_returns_body_unchanged() -> None:
    """An empty term set is a no-op, not an error."""
    body = "Nothing here matches anything."
    assert auto_link_glossary_terms(body, ()) == body


def test_empty_body_returns_empty() -> None:
    """An empty body is a no-op, not an error."""
    assert auto_link_glossary_terms("", (_LIQUID_STAKING,)) == ""


def test_longer_term_wins_over_a_shorter_substring_term() -> None:
    """If one term's phrase is a substring of another's, the LONGER phrase is tried first, so it isn't pre-empted by a shorter partial match."""
    short = GlossaryLinkTerm(slug="staking", term="staking", definition="Locking tokens for rewards.")
    long_term = GlossaryLinkTerm(
        slug="liquid-staking", term="liquid staking", definition="Staking with a tradeable receipt."
    )
    body = "Liquid staking is a form of staking."
    out = auto_link_glossary_terms(body, (short, long_term))
    # The longer phrase "liquid staking" should claim the first (overlapping) occurrence.
    assert "[Liquid staking](/glossary/liquid-staking" in out


def test_fetch_failure_fails_open_to_unchanged_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any error while resolving terms (e.g. Cassandra down) must never fail an article write -- body passes through unchanged."""
    from app.modules.newspaper import glossary_linker

    def _boom() -> None:
        raise RuntimeError("cassandra down")

    monkeypatch.setattr(glossary_linker, "published_terms_cached", _boom)
    body = "Liquid staking is popular."
    assert auto_link_glossary_terms(body) == body
