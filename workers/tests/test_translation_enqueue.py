"""Tests for article translation enqueue helpers."""

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
from app.modules.newspaper.tasks import publish_tasks as pt


def test_enqueue_missing_skips_existing_langs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enqueues ONE batch task (not one per language) covering only the languages missing from the article's stored translations."""
    calls: list[tuple[str, list]] = []

    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, args: calls.append((name, args)),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(
            body="English body",
            translations={"ar": "{}", "fa": "{}"},
        ),
    )

    n = pt.enqueue_missing_article_translations("article-1")
    assert len(calls) == 1
    name, args = calls[0]
    assert name == "app.tasks.newspaper.translate_article_batch"
    assert args[0] == "article-1"
    sent = args[1]
    assert n == len(sent)
    assert "ar" not in sent
    assert "fa" not in sent
    assert "ps" in sent
    assert "ru" in sent


def test_enqueue_missing_skips_task_when_nothing_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No send_task call at all once every language is already stored — not a batch task with an empty language list."""
    calls: list[tuple[str, list]] = []

    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, args: calls.append((name, args)),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(
            body="English body",
            translations=dict.fromkeys(ARTICLE_TRANSLATION_LANGS, "{}"),
        ),
    )

    n = pt.enqueue_missing_article_translations("article-1")
    assert n == 0
    assert calls == []


def test_translate_batch_task_skips_without_touching_local_translate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translate_article_batch_task short-circuits to "skipped" when every requested language is already stored, without ever calling into local_translate -- so a fully-translated article never imports transformers/torch, let alone loads a model."""
    from app.modules.ai import local_translate

    def _boom(**_kw: object) -> None:
        raise AssertionError("translate_article_batch must not be called when nothing is pending")

    monkeypatch.setattr(local_translate, "translate_article_batch", _boom)
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(
            title="T",
            summary="S",
            body="English body",
            translations={"fa": "{}", "ru": "{}"},
        ),
    )

    result = pt.translate_article_batch_task("article-1", ["fa", "ru"])
    assert result == {"status": "skipped", "reason": "already_translated", "langs": ["fa", "ru"]}


def test_translate_batch_task_persists_and_pings_per_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's on_language_done callback writes to Cassandra and fires the IndexNow ping per language -- same two side effects the retired per-language task had, now reached from inside the batch."""
    from app.modules.ai import local_translate

    written: list[tuple[str, dict]] = []
    pinged: list[str] = []

    def _fake_batch(
        *,
        target_languages: list[str],
        on_language_done: Callable[[str, dict], None],
        **_kw: object,
    ) -> dict:
        for lang in target_languages:
            on_language_done(lang, {"title": f"t-{lang}", "summary": "s", "body": "b"})
        return {"ok": list(target_languages), "failed": {}}

    monkeypatch.setattr(local_translate, "translate_article_batch", _fake_batch)
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(title="T", summary="S", body="B", translations={}),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.update_article_translations",
        lambda article_id, translations: written.append((article_id, translations)),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.indexnow.ping_translation",
        lambda _article_id, lang: pinged.append(lang),
    )

    result = pt.translate_article_batch_task("article-1", ["fa", "ru"])
    assert result["status"] == "ok"
    assert result["ok"] == ["fa", "ru"]
    assert [w[0] for w in written] == ["article-1", "article-1"]
    assert pinged == ["fa", "ru"]


def test_translate_batch_task_tracks_session_lifecycle_per_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task starts a translation_session before each language and finishes it 'ok'/'error' to match -- the session store's stale-reaper depends on every started row eventually being closed by the SAME task that opened it."""
    from app.modules.ai import local_translate

    def _fake_batch(
        *,
        target_languages: list[str],
        on_language_start: Callable[[str], None],
        on_language_done: Callable[[str, dict], None],
        on_language_error: Callable[[str, str], None],
        **_kw: object,
    ) -> dict:
        for lang in target_languages:
            on_language_start(lang)
            if lang == "ru":
                on_language_error(lang, "translation_error")
            else:
                on_language_done(lang, {"title": f"t-{lang}", "summary": "s", "body": "b"})
        return {"ok": [lang for lang in target_languages if lang != "ru"], "failed": {"ru": "translation_error"} if "ru" in target_languages else {}}

    monkeypatch.setattr(local_translate, "translate_article_batch", _fake_batch)
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(title="T", summary="S", body="B", translations={}),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.update_article_translations", lambda *_a, **_kw: None
    )

    started: list[str] = []
    finished: list[tuple[str, str]] = []

    def _fake_start(_article_id: str, lang: str) -> tuple:
        started.append(lang)
        return (f"session-{lang}", "ts")

    def _fake_finish(ref: tuple, *, status: str, error: str = "") -> bool:  # noqa: ARG001
        finished.append((ref[0], status))
        return True

    monkeypatch.setattr(
        "app.modules.ai.translation_session_store.start_translation_session", _fake_start
    )
    monkeypatch.setattr(
        "app.modules.ai.translation_session_store.finish_translation_session", _fake_finish
    )

    pt.translate_article_batch_task("article-1", ["fa", "ru"])

    assert started == ["fa", "ru"]
    assert finished == [("session-fa", "ok"), ("session-ru", "error")]


def test_translate_batch_task_reports_partial_on_any_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status is "partial", not "ok", when the batch's failed dict is non-empty."""
    from app.modules.ai import local_translate

    monkeypatch.setattr(
        local_translate,
        "translate_article_batch",
        lambda **_kw: {"ok": ["fa"], "failed": {"ru": "translation_error"}},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _id: SimpleNamespace(title="T", summary="S", body="B", translations={}),
    )

    result = pt.translate_article_batch_task("article-1", ["fa", "ru"])
    assert result["status"] == "partial"
    assert result["failed"] == {"ru": "translation_error"}
