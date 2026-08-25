"""Root-caused live 2026-08-10 (Pixel City / pixelcity-aetheralabs-es): _hold_for_review's post-compose review_queue_full() race used to discard a finished draft.

review_queue_full() runs AFTER the ~24-minute compose already produced real
content. The real protection against wasted Mistral spend is the pre-compose
check upstream in queue_drain_tasks.py; this post-compose check used to
discard the finished draft outright instead of storing it -- the same
"throw away a finished compose" failure already fixed once for the daily-cap
race (_stash_capped_compose_to_backlog, see
test_capped_compose_is_stashed_to_backlog_not_discarded in
test_drain_auto_publish_pacing.py). It must store and enqueue for review
instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.ai.content_signals import ContentSignals
from app.modules.newspaper.article_composer import ArticleComposeResult
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
from app.modules.newspaper.tasks import publish_tasks as pt


def _signals() -> ContentSignals:
    return ContentSignals(
        category="ecosystem",
        categories=("ecosystem",),
        relevance=0.9,
        publish_decision=None,
        confidence=0.5,
        storage_score=0.9,
    )


def test_review_queue_full_race_stores_and_enqueues_instead_of_discarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A review-bound draft that finishes composing after the queue filled must still be stored and enqueued, not discarded."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: True,
    )
    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True))[1],
    )
    enqueued: dict = {}
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review",
        lambda **kw: (enqueued.update(kw), "rid-1")[1],
    )
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, None, True))
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_domain_compose", lambda _domain: None
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_service_compose", lambda _service_id: None
    )

    row = SimpleNamespace(
        queue_id="q1",
        service_id="pixelcity-aetheralabs-es",
        scrape_url="https://pixelcity.aetheralabs.es/gallery",
    )
    composed = ArticleComposeResult(
        title="Pixel City generative art collection hits 246 of 450 mints on Algorand",
        summary="s",
        body="body text",
        composer="mistral",
    )

    out = pt._hold_for_review(
        row,
        {"txid": "", "round_num": 0},
        composed,
        topic=PublishTopic.GENERIC,
        publish_kind=PublishKind.CONTENT_UPDATE,
        compose_domain="aetheralabs.es",
        clf_category="ecosystem",
        clf_confidence=0.5,
        signals=_signals(),
        gate_enforced_review=False,
        hold_reason="",
        hero_image="",
        image_field="",
        route_to_backlog=False,
        page_text_for_clf="page text",
    )

    assert out["status"] == "review"
    assert stored["publish_to_feed"] is False
    assert stored["title"] == composed.title
    assert enqueued["metadata"]["article_id"] == out["article_id"]


def test_route_to_backlog_ignores_review_queue_full_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backlog-bound (already-approved) drafts never looked at review_queue_full at all -- unchanged."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: pytest.fail("route_to_backlog must not even check review_queue_full"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True),  # noqa: ARG005
    )
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, None, True))
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_domain_compose", lambda _domain: None
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_service_compose", lambda _service_id: None
    )
    executed: list = []

    class _FakeSession:
        def execute(self, stmt: str, params: tuple | None = None) -> None:
            executed.append((stmt, params))

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)

    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com/",
    )
    composed = ArticleComposeResult(title="T", summary="s", body="body", composer="mistral")

    out = pt._hold_for_review(
        row,
        {"txid": "", "round_num": 0},
        composed,
        topic=PublishTopic.GENERIC,
        publish_kind=PublishKind.CONTENT_UPDATE,
        compose_domain="",
        clf_category="ecosystem",
        clf_confidence=0.5,
        signals=_signals(),
        gate_enforced_review=False,
        hold_reason="",
        hero_image="",
        image_field="",
        route_to_backlog=True,
        page_text_for_clf="page text",
    )

    assert out["status"] == "approved_backlog"
