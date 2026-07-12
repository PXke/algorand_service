from __future__ import annotations

from app.modules.search.core.tokenize import build_article_search_tokens


def test_us_in_body_expands_usa_cluster() -> None:
    tokens = build_article_search_tokens(
        title="Treasury flows",
        summary="",
        body="Investors sent funds back to US markets overnight.",
    )
    assert "us" in tokens
    assert "usa" in tokens


def test_usability_does_not_add_usa_cluster() -> None:
    tokens = build_article_search_tokens(
        title="Usability study",
        summary="",
        body="Improving usability for newcomers.",
    )
    assert "usa" not in tokens
    assert "us" not in tokens


def test_tags_are_indexed_as_tokens() -> None:
    tokens = build_article_search_tokens(
        title="Story",
        summary="",
        body="Body",
        tags=["Governance", "DeFi"],
    )
    assert "governance" in tokens
    assert "defi" in tokens


def test_dotted_acronym_normalizes() -> None:
    tokens = build_article_search_tokens(
        title="Policy shift",
        summary="",
        body="Regulators in the U.S. announced new rules.",
    )
    assert "us" in tokens
    assert "usa" in tokens
