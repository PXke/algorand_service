"""algorand_shared.article_statements: the feed/tag-listing statements select translated_titles (the lightweight lang -> JSON {title, summary} column), not the full translations map -- migration 087. The single-article detail statement (GET_FULL_BY_ID) is untouched: it still selects both columns."""

from __future__ import annotations

from algorand_shared.article_statements import ArticlesStmts, ArticleTagIndexStmts


def _cql(cls: type, name: str) -> str:
    """Read a `_Stmt`'s raw CQL text via the class `__dict__` (bypasses the descriptor's `__get__`, which calls `prepare_cached` and needs a live session)."""
    return cls.__dict__[name].cql


def test_list_published_page_selects_translated_titles_not_translations() -> None:
    cql = _cql(ArticlesStmts, "LIST_PUBLISHED_PAGE")
    assert "translated_titles" in cql
    assert "translations" not in cql


def test_article_tag_index_list_page_selects_translated_titles_not_translations() -> None:
    cql = _cql(ArticleTagIndexStmts, "LIST_PAGE")
    assert "translated_titles" in cql
    assert "translations" not in cql


def test_article_tag_index_list_recent_selects_translated_titles_not_translations() -> None:
    cql = _cql(ArticleTagIndexStmts, "LIST_RECENT")
    assert "translated_titles" in cql
    assert "translations" not in cql


def test_get_full_by_id_still_selects_the_full_translations_map() -> None:
    """The single-article detail read is UNCHANGED -- it still needs the full translations map (title+summary+body per language), not just the lightweight companion."""
    cql = _cql(ArticlesStmts, "GET_FULL_BY_ID")
    assert "translations" in cql
    assert "translated_titles" in cql


def test_articles_insert_carries_both_columns() -> None:
    cql = _cql(ArticlesStmts, "INSERT")
    assert "translations" in cql
    assert "translated_titles" in cql
    # 25 columns -> 25 placeholders.
    assert cql.count("?") == 25


def test_article_tag_index_insert_carries_both_columns() -> None:
    cql = _cql(ArticleTagIndexStmts, "INSERT")
    assert "translations" in cql
    assert "translated_titles" in cql
    assert cql.count("?") == 14


def test_update_translations_merges_both_maps_in_one_statement() -> None:
    cql = _cql(ArticlesStmts, "UPDATE_TRANSLATIONS")
    assert "translations = translations + ?" in cql
    assert "translated_titles = translated_titles + ?" in cql


def test_clear_translations_clears_both_columns_together() -> None:
    cql = _cql(ArticlesStmts, "CLEAR_TRANSLATIONS")
    assert cql.startswith("DELETE translations, translated_titles FROM")
