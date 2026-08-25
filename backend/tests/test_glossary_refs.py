"""extract_glossary_slugs is what feeds Typesense's glossary_slugs field (backend/app/core/typesense_client.py, workers/app/modules/search/core/indexer.py) -- a wrong match here means a glossary term page's "referenced in" list either misses an article or picks up a false positive."""

from __future__ import annotations

from algorand_shared.glossary_refs import extract_glossary_slugs


def test_extracts_slug_with_title() -> None:
    """The linker's usual output shape (`[text](/glossary/slug "title")`) yields the slug."""
    body = 'See [ARC-27](/glossary/arc-27 "A standard for wallet interop") for details.'
    assert extract_glossary_slugs(body) == ["arc-27"]


def test_extracts_slug_without_title() -> None:
    """A glossary link with no title segment (empty definition) still yields the slug."""
    body = "Check out [staking](/glossary/staking)."
    assert extract_glossary_slugs(body) == ["staking"]


def test_unions_across_multiple_bodies_same_slug() -> None:
    """The whole point: the SAME slug found in the English body and a translated body is one entry, not two -- a slug is locale-independent even though the anchor text/title differ per language."""
    en = 'Read about [staking](/glossary/staking "Locking tokens").'
    fr = 'En savoir plus sur le [jalonnement](/glossary/staking "Verrouillage de jetons").'
    assert extract_glossary_slugs(en, fr) == ["staking"]


def test_unions_distinct_slugs_across_bodies() -> None:
    """Distinct slugs found across different bodies are all kept, not just the first body's set."""
    en = "[ARC-27](/glossary/arc-27) wallets support [staking](/glossary/staking)."
    fr = "Seul [jalonnement](/glossary/staking) est mentionné ici."
    assert extract_glossary_slugs(en, fr) == ["arc-27", "staking"]


def test_ignores_non_glossary_links() -> None:
    """An external citation link or an article link is not a glossary reference."""
    body = "See [our docs](https://example.com/docs) and [source](/news/articles/abc)."
    assert extract_glossary_slugs(body) == []


def test_skips_none_and_empty_bodies() -> None:
    """Missing/blank bodies (an untranslated language, an empty draft) are simply skipped, not an error."""
    assert extract_glossary_slugs(None, "", "  ") == []
    assert extract_glossary_slugs() == []


def test_sorted_deterministic_order() -> None:
    """Output is sorted regardless of the order slugs first appear in the body, for stable diffing/tests."""
    body = "[z-term](/glossary/z-term) then [a-term](/glossary/a-term)"
    assert extract_glossary_slugs(body) == ["a-term", "z-term"]
