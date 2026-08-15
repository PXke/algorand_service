"""Every writer tool that reads the live feed must not surface the article currently being recomposed as if it were independent prior coverage -- the same self-reinforcement risk already fixed once for editorial-brief recompose (reads the original brief, never its own prior output).

Root-caused live 2026-08-11 (Lumi Rogue incident follow-up): recent_articles,
search_platform, trending_articles, source_history and get_article all read
the live published feed with zero awareness that one article_id is "itself"
mid-recompose.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.ai import writer_tools as wt


def _row(article_id: str, title: str, service_id: str = "svc") -> SimpleNamespace:
    return SimpleNamespace(
        article_id=article_id,
        title=title,
        summary="summary",
        service_id=service_id,
        published_at_epoch=100,
    )


def test_recomposing_article_is_a_noop_when_none() -> None:
    """Outside a recompose, the context var stays unset -- no filtering happens."""
    with wt.recomposing_article(None):
        assert wt._recomposing_article_id.get() is None


def test_recomposing_article_resets_after_the_block() -> None:
    """The marker must not leak into unrelated tool calls after the compose finishes."""
    with wt.recomposing_article("self-id"):
        assert wt._recomposing_article_id.get() == "self-id"
    assert wt._recomposing_article_id.get() is None


def test_recent_articles_excludes_the_self_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """recent_articles must never surface the article currently being recomposed."""
    rows = [_row("self-id", "Old title"), _row("other-id", "Other title")]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles", lambda limit: rows  # noqa: ARG005
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_recent_articles(limit=5)
    ids = [a["article_id"] for a in result["articles"]]
    assert "self-id" not in ids
    assert "other-id" in ids


def test_search_platform_excludes_the_self_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_platform must never surface the article currently being recomposed."""
    rows = [_row("self-id", "Lumi Rogue thing"), _row("other-id", "Lumi Rogue other")]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles", lambda limit: rows  # noqa: ARG005
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_search_platform("Lumi Rogue")
    ids = [m["article_id"] for m in result["matches"]]
    assert "self-id" not in ids
    assert "other-id" in ids


def test_trending_articles_excludes_the_self_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """trending_articles must never surface the article currently being recomposed."""
    rows = [_row("self-id", "Old title"), _row("other-id", "Other title")]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles", lambda limit: rows  # noqa: ARG005
    )
    monkeypatch.setattr(
        "app.modules.newspaper.view_counts.get_views_bulk",
        lambda ids: dict.fromkeys(ids, 5),
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_trending_articles(limit=5)
    titles = [a["title"] for a in result["articles"]]
    assert "Old title" not in titles
    assert "Other title" in titles


def test_source_history_excludes_the_self_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """source_history must never surface the article currently being recomposed."""
    rows = [
        _row("self-id", "Old coverage", service_id="lumirogue-com"),
        _row("other-id", "Earlier coverage", service_id="lumirogue-com"),
    ]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles", lambda limit: rows  # noqa: ARG005
    )
    monkeypatch.setattr(
        "app.modules.chain_tail.registry_cache.load_enabled_services",
        lambda: [
            SimpleNamespace(
                service_id="lumirogue-com",
                display_name="Lumi Rogue",
                scrape_url="https://lumirogue.com",
            )
        ],
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_source_history("lumirogue.com")
    titles = [a["title"] for a in result["articles"]]
    assert "Old coverage" not in titles
    assert "Earlier coverage" in titles


def test_get_article_flags_the_self_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A direct id lookup is intentional -- flag it clearly instead of excluding it."""
    detail = SimpleNamespace(
        article_id="self-id",
        title="t",
        summary="s",
        source_url="https://example.com",
        published_at_epoch=1,
        body="body text",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article", lambda aid: detail  # noqa: ARG005
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_get_article("self-id")
    assert result["is_the_article_currently_being_recomposed"] is True
    assert "warning" in result


def test_get_article_does_not_flag_a_different_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetching a genuinely different article during a recompose is unaffected."""
    detail = SimpleNamespace(
        article_id="other-id",
        title="t",
        summary="s",
        source_url="https://example.com",
        published_at_epoch=1,
        body="body text",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article", lambda aid: detail  # noqa: ARG005
    )
    with wt.recomposing_article("self-id"):
        result = wt._tool_get_article("other-id")
    assert "is_the_article_currently_being_recomposed" not in result
