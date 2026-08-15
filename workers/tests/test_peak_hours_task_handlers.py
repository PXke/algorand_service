"""PeakHoursBlockedError handling in publish_tasks.py's three MistralError catch sites (2026-08-15): each must report a routine "skipped_peak_hours" skip via logger.info, never the "mistral_failed"/logger.error path used for real failures, and must preserve every side effect (review restoration) a genuine failure would also trigger."""

from __future__ import annotations

from typing import Never

import pytest

from app.modules.ai.mistral_client import PeakHoursBlockedError
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks import publish_tasks as pt


def _row() -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id="q1",
        priority=5,
        topic="",
        publish_kind="discovery",
        service_id="svc",
        display_name="Svc",
        scrape_url="https://example.com/x",
        payload={},
        created_at_epoch=0,
    )


def _raise_peak_blocked(**_kw: object) -> Never:
    raise PeakHoursBlockedError("peak hours (DeepSeek billing) — next off-peak start at ...")


def test_compose_or_error_reports_skipped_peak_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """_compose_or_error (the drain/queue-row-now path) must map PeakHoursBlockedError to a routine skip status, not mistral_failed."""
    monkeypatch.setattr(pt, "compose_scrape_article", _raise_peak_blocked)

    _composed, error = pt._compose_or_error(
        _row(),
        {},
        topic=PublishTopic.GENERIC,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        mistral_only=False,
        enrichment_block="",
        first_coverage=True,
    )
    assert error is not None
    assert error["status"] == "skipped_peak_hours"


def test_recompose_via_writer_reports_skipped_peak_hours_and_restores_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_recompose_via_writer must both report skipped_peak_hours AND still call enqueue_classifier_review to restore the original proposal -- the same recovery a genuine Mistral failure triggers, so a peak-hours defer never silently drops the review."""
    monkeypatch.setattr(pt, "compose_scrape_article", _raise_peak_blocked)

    restored: dict = {}

    def _fake_enqueue(**kwargs: object) -> None:
        restored.update(kwargs)

    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review", _fake_enqueue
    )

    _composed, error = pt._recompose_via_writer(
        review_id="rev1",
        url="https://example.com/x",
        page_text="text",
        page_title="title",
        category="cat",
        storage_score=0.5,
        kind="web",
        old_article_id="art1",
    )
    assert error is not None
    assert error["status"] == "skipped_peak_hours"
    assert restored["url"] == "https://example.com/x"
    assert restored["metadata"]["article_id"] == "art1"


def test_recompose_published_compose_reports_skipped_peak_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_recompose_published_compose (the archive-refresh path) must map PeakHoursBlockedError to a routine skip, not a self.retry() or a mistral_failed status."""
    monkeypatch.setattr(pt, "compose_scrape_article", _raise_peak_blocked)

    class _FakeTask:
        def retry(self, *_a: object, **_kw: object) -> Never:
            raise AssertionError("must not retry on a peak-hours defer")

    _composed, error = pt._recompose_published_compose(
        _FakeTask(),
        article_id="art1",
        service_id="svc",
        source_url="https://example.com/x",
        page_text="text",
        page_title="title",
        brief_for_recompose=None,
    )
    assert error is not None
    assert error["status"] == "skipped_peak_hours"
