"""fanout_after_publish / record_compose_cadence (publish_fanout.py, W4-A 2026-08-28).

Before this module existed, `_finalize_publish`, `_release_pending_feed_
backlog`, and `apply_recomposed_article` each reimplemented the "an article
just went live" tail independently -- most visibly, the backlog-release copy
never called index_article at all, so a released article silently never
entered Typesense search. These tests cover the shared function directly:
every step fires, a failure in one step doesn't block the others, and
distribute=False actually skips distribution. Regression coverage for each
real call site now going through this shared function lives in
test_finalize_publish_fanout.py, test_drain_approved_feed_queue_timestamp.py,
and test_apply_recomposed_article_draft_guard.py.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.newspaper import publish_fanout

_ARTICLE_ID = "33333333-3333-3333-3333-333333333333"


def _fake_article(**overrides: object) -> SimpleNamespace:
    base = {
        "article_id": _ARTICLE_ID,
        "service_id": "perawallet.app",
        "title": "A Title",
        "summary": "A Summary",
        "body": "A Body",
        "published_at_epoch": 1_700_000_000,
        "source_url": "https://perawallet.app/news/thing",
        "slug": "a-title",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _patch_steps(monkeypatch: pytest.MonkeyPatch, *, article: SimpleNamespace | None) -> dict:
    """Wire every fanout_after_publish step to a mock/fake, return them keyed by name.

    publish_fanout.py imports get_article/index_article/index_crawled_page/
    ensure_article_slug/ping_article/distribute_article at module top (not
    function-local -- see CLAUDE.md Sec.3, no circular import forces them
    local), so each is patched on publish_fanout's own bound name, not the
    origin module -- patching the origin wouldn't reach a name already bound
    into this module's namespace at import time.
    """
    monkeypatch.setattr(publish_fanout, "get_article", lambda _aid: article)
    index_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "index_article", index_mock)
    crawled_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "index_crawled_page", crawled_mock)
    translate_mock = MagicMock()
    # enqueue_article_translations stays a genuine function-local import in
    # fanout_after_publish (circular import: publish_tasks.py imports this
    # module) -- patched at its origin module, same as any other caller.
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations", translate_mock
    )
    monkeypatch.setattr(publish_fanout, "ensure_article_slug", lambda _aid, _title: "a-title")
    ping_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "ping_article", ping_mock)
    distribute_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "distribute_article", distribute_mock)
    return {
        "index": index_mock,
        "crawled": crawled_mock,
        "translate": translate_mock,
        "ping": ping_mock,
        "distribute": distribute_mock,
    }


def test_fanout_after_publish_runs_every_step_when_distribute_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every fanout step fires with the article's own (freshly re-read) fields."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)

    result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    assert result == {"status": "ok", "article_id": _ARTICLE_ID}
    steps["index"].delay.assert_called_once_with(
        article_id=_ARTICLE_ID,
        title="A Title",
        summary="A Summary",
        body="A Body",
        service_id="perawallet.app",
        published_at_epoch=1_700_000_000,
    )
    steps["translate"].assert_called_once_with(_ARTICLE_ID)
    steps["ping"].assert_called_once_with(_ARTICLE_ID, slug="a-title")
    steps["distribute"].delay.assert_called_once_with(article_id=_ARTICLE_ID)
    # No page_text passed -- the crawled-page index step is skipped, matching
    # every call site except _finalize_publish's direct-crawl path.
    steps["crawled"].delay.assert_not_called()


def test_fanout_after_publish_distribute_false_skips_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """apply_recomposed_article's distribute=False: everything else still runs."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)

    result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=False)

    assert result["status"] == "ok"
    steps["index"].delay.assert_called_once()
    steps["translate"].assert_called_once()
    steps["ping"].assert_called_once()
    steps["distribute"].delay.assert_not_called()


def test_fanout_after_publish_page_text_indexes_crawled_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_finalize_publish's direct-crawl path: page_text present -> index_crawled_page fires with the article's own source_url/service_id."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)

    publish_fanout.fanout_after_publish(
        _ARTICLE_ID, distribute=True, page_text="body text", page_title="Page Title"
    )

    steps["crawled"].delay.assert_called_once_with(
        url="https://perawallet.app/news/thing",
        title="Page Title",
        text="body text",
        service_id="perawallet.app",
    )


def test_fanout_after_publish_index_failure_does_not_block_other_steps(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Celery broker hiccup on the search-index enqueue must not stop translations/IndexNow/distribution, and must not raise out of fanout_after_publish."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)
    steps["index"].delay.side_effect = RuntimeError("broker unreachable")

    with caplog.at_level(logging.WARNING):
        result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    assert result["status"] == "ok"
    steps["translate"].assert_called_once()
    steps["ping"].assert_called_once()
    steps["distribute"].delay.assert_called_once()
    assert any("failed to queue search index" in rec.message for rec in caplog.records)


def test_fanout_after_publish_indexnow_failure_is_logged_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An IndexNow ping failure must be logged with a traceback, not a bare except-pass, and must not block distribution."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)
    steps["ping"].side_effect = RuntimeError("indexnow unreachable")

    with caplog.at_level(logging.WARNING):
        result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    assert result["status"] == "ok"
    steps["distribute"].delay.assert_called_once()
    matches = [rec for rec in caplog.records if "IndexNow ping failed" in rec.message]
    assert matches
    assert matches[0].exc_info is not None


def test_fanout_after_publish_distribute_failure_is_logged_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A distribution-dispatch failure must be logged, not swallowed silently, and must not raise out of fanout_after_publish."""
    article = _fake_article()
    steps = _patch_steps(monkeypatch, article=article)
    steps["distribute"].delay.side_effect = RuntimeError("distribution dispatcher down")

    with caplog.at_level(logging.WARNING):
        result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    assert result["status"] == "ok"
    assert any("failed to queue distribution" in rec.message for rec in caplog.records)


def test_fanout_after_publish_missing_article_returns_error_without_crashing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A vanished article_id (read-after-write miss) must return an error result, not crash, and must not fire any fanout step."""
    steps = _patch_steps(monkeypatch, article=None)

    with caplog.at_level(logging.WARNING):
        result = publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    assert result == {"status": "error", "reason": "article_not_found", "article_id": _ARTICLE_ID}
    steps["index"].delay.assert_not_called()
    steps["translate"].assert_not_called()
    steps["ping"].assert_not_called()
    steps["distribute"].delay.assert_not_called()


def test_fanout_after_publish_falls_back_to_now_when_published_at_epoch_falsy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0/None published_at_epoch (e.g. an unparsed timestamp) must not be sent to the search index as-is -- it would sort the article as epoch-zero."""
    article = _fake_article(published_at_epoch=0)
    steps = _patch_steps(monkeypatch, article=article)

    publish_fanout.fanout_after_publish(_ARTICLE_ID, distribute=True)

    _, kwargs = steps["index"].delay.call_args
    assert kwargs["published_at_epoch"] > 0


# ── record_compose_cadence ──────────────────────────────────────────────────


def test_record_compose_cadence_stamps_domain_and_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both cooldown stamps fire with the given domain/service_id."""
    domain_calls: list[str] = []
    service_calls: list[str] = []
    monkeypatch.setattr(publish_fanout, "record_domain_compose", domain_calls.append)
    monkeypatch.setattr(publish_fanout, "record_service_compose", service_calls.append)

    publish_fanout.record_compose_cadence(compose_domain="perawallet.app", service_id="pera")

    assert domain_calls == ["perawallet.app"]
    assert service_calls == ["pera"]


def test_record_compose_cadence_skips_empty_domain_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty compose_domain/service_id must not stamp either cooldown."""
    domain_mock = MagicMock()
    service_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "record_domain_compose", domain_mock)
    monkeypatch.setattr(publish_fanout, "record_service_compose", service_mock)

    publish_fanout.record_compose_cadence(compose_domain="", service_id="")

    domain_mock.assert_not_called()
    service_mock.assert_not_called()


def test_record_compose_cadence_marks_brief_only_when_editorial_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_brief_run only fires when is_editorial_assignment=True, with the given brief/article ids."""
    monkeypatch.setattr(publish_fanout, "record_domain_compose", lambda _d: None)
    monkeypatch.setattr(publish_fanout, "record_service_compose", lambda _s: None)
    mark_mock = MagicMock()
    monkeypatch.setattr(publish_fanout, "mark_brief_run", mark_mock)

    publish_fanout.record_compose_cadence(
        compose_domain="d.com", service_id="svc", is_editorial_assignment=False
    )
    mark_mock.assert_not_called()

    publish_fanout.record_compose_cadence(
        compose_domain="d.com",
        service_id="svc",
        article_id="aid",
        is_editorial_assignment=True,
        brief_id="brief-1",
    )
    mark_mock.assert_called_once_with(brief_id="brief-1", article_id="aid")


def test_record_compose_cadence_suppresses_mark_brief_run_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_brief_run failing must not blow up the publish path that just successfully stored/published the article."""
    monkeypatch.setattr(publish_fanout, "record_domain_compose", lambda _d: None)
    monkeypatch.setattr(publish_fanout, "record_service_compose", lambda _s: None)

    def _boom(**_kw: object) -> None:
        raise RuntimeError("cassandra blip")

    monkeypatch.setattr(publish_fanout, "mark_brief_run", _boom)

    # Must not raise.
    publish_fanout.record_compose_cadence(
        compose_domain="d.com", service_id="svc", is_editorial_assignment=True, brief_id="b1"
    )
