"""Slug generation is a permanent-URL decision, so pin its edges.

Every case here is one that would have shipped a bad URL: a slug that changes
between runs, one that collides silently, one that percent-escapes into noise,
or one that truncates mid-word.
"""

from __future__ import annotations

import pytest

from algorand_shared.slugs import MAX_SLUG_CHARS, slugify, unique_slug


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AlgoRank debuts 1K-project directory", "algorank-debuts-1k-project-directory"),
        ("  Spaced   out  ", "spaced-out"),
        ("Algorand's Über Rollup", "algorands-uber-rollup"),  # folded, not escaped
        ("Algorand’s curly apostrophe", "algorands-curly-apostrophe"),
        ("C++ & Rust: a comparison", "c-rust-a-comparison"),
        ("DeFi/NFT split", "defi-nft-split"),
        ("trailing --- dashes ---", "trailing-dashes"),
        ("under_scores_too", "under-scores-too"),
    ],
)
def test_slugify_shapes(title: str, expected: str) -> None:
    """Punctuation, case, unicode and separators all reduce to a clean slug."""
    assert slugify(title) == expected


@pytest.mark.parametrize("title", ["", "   ", "…", "。。。", "🎉🎉"])
def test_slugify_returns_empty_when_nothing_survives(title: str) -> None:
    """A title with no ASCII-able characters yields "" so the caller can fall back."""
    assert slugify(title) == ""


def test_slugify_clamps_on_a_word_boundary() -> None:
    """Long titles are cut between words, never mid-word."""
    slug = slugify("word " * 40)
    assert len(slug) <= MAX_SLUG_CHARS
    assert not slug.endswith("-")
    assert "wor-" not in slug  # would signal a mid-word cut


def test_slugify_is_stable() -> None:
    """Same title, same slug — a backfill must be re-runnable without moving URLs."""
    title = "Algorand Foundation Launches New Tool"
    assert slugify(title) == slugify(title)


def test_unique_slug_first_claimant_keeps_the_bare_slug() -> None:
    """The first article with a title owns the unsuffixed URL."""
    assert unique_slug("Weekly digest", fallback="id1", is_taken=lambda s: False) == "weekly-digest"


def test_unique_slug_suffixes_later_duplicates() -> None:
    """Collisions queue behind the original as -2, -3, ... rather than overwriting."""
    taken = {"weekly-digest"}
    second = unique_slug("Weekly digest", fallback="id2", is_taken=lambda s: s in taken)
    assert second == "weekly-digest-2"
    taken.add(second)
    third = unique_slug("Weekly digest", fallback="id3", is_taken=lambda s: s in taken)
    assert third == "weekly-digest-3"


def test_unique_slug_falls_back_to_the_article_id() -> None:
    """An unslugifiable title still produces a resolvable URL."""
    got = unique_slug("🎉", fallback="afeeec91-dc1a-4cba-88b3-7447ac3ee2c3", is_taken=lambda s: False)
    assert got == "afeeec91-dc1a-4cba-88b3-7447ac3ee2c3"


def test_unique_slug_suffix_does_not_blow_the_length_budget() -> None:
    """Suffixes apply to the clamped base, so duplicates stay a sane length."""
    long_title = "word " * 40
    taken = {slugify(long_title)}
    got = unique_slug(long_title, fallback="id", is_taken=lambda s: s in taken)
    assert got.endswith("-2")
    assert len(got) <= MAX_SLUG_CHARS + 4


def test_unique_slug_raises_rather_than_looping_forever() -> None:
    """A pathological collision surfaces as an error, not a hang."""
    with pytest.raises(ValueError, match="could not find a free slug"):
        unique_slug("Same", fallback="id", is_taken=lambda s: True)
