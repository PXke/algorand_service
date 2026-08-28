"""translate_article_task (the single-language legacy path) must persist BOTH translations and the lightweight translated_titles derived from the same result, in one update_article_translations call (migration 087)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.newspaper.tasks import publish_tasks as pt


def test_translate_article_task_persists_translations_and_translated_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful translation writes the full {title, summary, body} into translations and only {title, summary} into translated_titles."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(
            body="English body", title="English title", summary="English summary",
            translations={}, slug="a-slug",
        ),
    )  # fmt: skip
    monkeypatch.setattr(
        "app.modules.ai.llm_compose.translate_article",
        lambda **_kw: {
            "title": "Titre francais",
            "summary": "Resume francais",
            "body": "Corps francais",
        },
    )
    written: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.update_article_translations",
        lambda article_id, translations, translated_titles: written.append(
            (article_id, translations, translated_titles)
        ),
    )
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_translation", lambda *_a, **_kw: None)

    result = pt.translate_article_task("article-1", "fr")

    assert result["status"] == "ok"
    assert len(written) == 1
    article_id, translations, translated_titles = written[0]
    assert article_id == "article-1"

    import json

    full = json.loads(translations["fr"])
    assert full == {
        "title": "Titre francais",
        "summary": "Resume francais",
        "body": "Corps francais",
    }

    light = json.loads(translated_titles["fr"])
    assert light == {"title": "Titre francais", "summary": "Resume francais"}
    assert "body" not in light
    assert "Corps francais" not in translated_titles["fr"]


def test_translate_article_task_skips_when_language_already_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Already-translated languages never reach update_article_translations at all."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(body="English body", translations={"fr": "{}"}),
    )
    called = []
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.update_article_translations",
        lambda *a, **kw: called.append((a, kw)),
    )

    result = pt.translate_article_task("article-1", "fr")

    assert result == {"status": "skipped", "reason": "already_translated", "lang": "fr"}
    assert called == []
