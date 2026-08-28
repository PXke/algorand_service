"""_finalize_publish (workers/app/modules/newspaper/tasks/publish_tasks.py) regression coverage, W4-A (2026-08-28).

_finalize_publish previously reimplemented the "an article just went live"
fanout inline (search index, IndexNow, translations, distribution) and the
record_domain_compose/record_service_compose/mark_brief_run compose-cadence
block, both now shared via publish_fanout.py. These tests pin down that it
calls the shared functions with the right arguments instead of drifting back
into its own copy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.newspaper.article_composer import ArticleComposeResult
from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks import publish_tasks

_ARTICLE_ID = "44444444-4444-4444-4444-444444444444"


def _row(**overrides: object) -> QueuedPublishRow:
    base: dict[str, object] = {
        "queue_id": "q1",
        "priority": 1,
        "topic": "community_recap",
        "publish_kind": "content_update",
        "service_id": "perawallet.app",
        "display_name": "Pera Wallet",
        "scrape_url": "https://perawallet.app/news/thing",
        "payload": {},
        "created_at_epoch": 1_700_000_000,
    }
    base.update(overrides)
    return QueuedPublishRow(**base)


def _composed(**overrides: object) -> ArticleComposeResult:
    base: dict[str, object] = {
        "title": "A Title",
        "summary": "A Summary",
        "body": "A Body",
        "composer": "deepseek",
    }
    base.update(overrides)
    return ArticleComposeResult(**base)


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    insert_mock = MagicMock(return_value=_ARTICLE_ID)
    monkeypatch.setattr(publish_tasks, "insert_article", insert_mock)
    fanout_mock = MagicMock(return_value={"status": "ok", "article_id": _ARTICLE_ID})
    monkeypatch.setattr(publish_tasks, "fanout_after_publish", fanout_mock)
    monkeypatch.setattr(publish_tasks, "record_compose_cadence", MagicMock())
    return insert_mock, fanout_mock


def test_finalize_publish_calls_shared_fanout_with_distribute_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direct-publish path is genuinely new content -- distribute=True."""
    _insert_mock, fanout_mock = _patch_common(monkeypatch)

    result = publish_tasks._finalize_publish(
        _row(),
        {},
        _composed(),
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.COMMUNITY_RECAP,
        tier=PublishTier.STANDARD,
        compose_domain="perawallet.app",
    )

    assert result["status"] == "published"
    assert result["article_id"] == _ARTICLE_ID
    fanout_mock.assert_called_once_with(_ARTICLE_ID, distribute=True, page_text="", page_title="")


def test_finalize_publish_forwards_crawled_page_text_to_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh crawl's page_text/page_title (in payload) must reach fanout_after_publish so it can index the crawled page too."""
    _insert_mock, fanout_mock = _patch_common(monkeypatch)

    publish_tasks._finalize_publish(
        _row(),
        {"page_text": "the raw crawled text", "page_title": "Raw Page Title"},
        _composed(),
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.COMMUNITY_RECAP,
        tier=PublishTier.STANDARD,
        compose_domain="perawallet.app",
    )

    fanout_mock.assert_called_once_with(
        _ARTICLE_ID,
        distribute=True,
        page_text="the raw crawled text",
        page_title="Raw Page Title",
    )


def test_finalize_publish_records_compose_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    """The record_domain_compose/record_service_compose/mark_brief_run block, collapsed onto the shared record_compose_cadence, still fires on direct publish."""
    _insert_mock, _fanout_mock = _patch_common(monkeypatch)
    cadence_mock = MagicMock()
    monkeypatch.setattr(publish_tasks, "record_compose_cadence", cadence_mock)

    publish_tasks._finalize_publish(
        _row(payload={"source_kind": "editorial_assignment", "brief_id": "brief-9"}),
        {"source_kind": "editorial_assignment", "brief_id": "brief-9"},
        _composed(),
        hero_image="",
        image_field="",
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        topic=PublishTopic.COMMUNITY_RECAP,
        tier=PublishTier.STANDARD,
        compose_domain="perawallet.app",
    )

    cadence_mock.assert_called_once_with(
        compose_domain="perawallet.app",
        service_id="perawallet.app",
        article_id=_ARTICLE_ID,
        is_editorial_assignment=True,
        brief_id="brief-9",
    )


def test_finalize_publish_releases_slot_and_reraises_on_insert_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed insert_article must hand the daily-cap slot back and re-raise -- never silently swallow a failed write (see publish_daily_guard.release_publish_slot)."""
    monkeypatch.setattr(
        publish_tasks, "insert_article", MagicMock(side_effect=RuntimeError("cassandra down"))
    )
    fanout_mock = MagicMock()
    monkeypatch.setattr(publish_tasks, "fanout_after_publish", fanout_mock)
    cadence_mock = MagicMock()
    monkeypatch.setattr(publish_tasks, "record_compose_cadence", cadence_mock)
    release_mock = MagicMock()
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.release_publish_slot", release_mock
    )

    with pytest.raises(RuntimeError, match="cassandra down"):
        publish_tasks._finalize_publish(
            _row(),
            {},
            _composed(),
            hero_image="",
            image_field="",
            publish_kind=PublishKind.CONTENT_UPDATE,
            topic=PublishTopic.COMMUNITY_RECAP,
            tier=PublishTier.STANDARD,
            compose_domain="perawallet.app",
        )

    release_mock.assert_called_once_with(tier=PublishTier.STANDARD)
    # Nothing was ever published -- neither fanout nor compose-cadence
    # bookkeeping must run against a non-existent article.
    fanout_mock.assert_not_called()
    cadence_mock.assert_not_called()
