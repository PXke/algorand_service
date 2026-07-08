"""Tests for article translation enqueue helpers."""

from types import SimpleNamespace

from app.modules.newspaper.tasks import publish_tasks as pt


def test_enqueue_missing_skips_existing_langs(monkeypatch):
    sent: list[str] = []

    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, args: sent.append(args[1]),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(
            body="English body",
            translations={"ar": "{}", "fa": "{}"},
        ),
    )

    n = pt.enqueue_missing_article_translations("article-1")
    assert n == len(sent)
    assert "ar" not in sent
    assert "fa" not in sent
    assert "ps" in sent
    assert "ru" in sent
