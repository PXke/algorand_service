"""Every publish_tasks.py write path that used to call security.sanitize_body now uses the real nh3 sanitizer.

W1-B: security.sanitize_body was a regex-only <script>-tag stripper -- no
on*= handler / javascript:/data: URL stripping. All 5 call sites here now
call the real nh3 allowlist sanitizer (article_store._sanitize_body), the
same one insert_stored_article/insert_article already re-apply internally on
every one of these paths. These tests prove the malicious markup is stripped
at THIS call (the function's own _sanitize_body call), not just redundantly
again inside insert_stored_article -- mirroring
test_run_article_edit_sanitizes_body_before_storing (test_article_edit_service.py)
and test_article_store_sanitizes_body.py's pattern of asserting on the actual
kwargs/bind-params a write call receives.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from app.modules.newspaper.article_composer import ArticleComposeResult
from app.modules.newspaper.publish_policy import PublishKind, PublishTier, PublishTopic
from app.modules.newspaper.tasks import publish_tasks as pt

_MALICIOUS_BODY = (
    '<p onclick="alert(1)">Hello</p>'
    "<script>alert(2)</script>"
    '<a href="javascript:alert(3)">click</a>'
    '<img src="x" onerror="alert(4)">'
    " world"
)


def _assert_sanitized(body: str) -> None:
    assert "<script" not in body
    assert "alert(2)" not in body
    assert "onclick" not in body
    assert "onerror" not in body
    assert "javascript:" not in body
    assert "Hello" in body
    assert "world" in body


def _wire_domain_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_domain_compose", lambda _domain: None
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.record_service_compose", lambda _service_id: None
    )


def test_stash_capped_compose_to_backlog_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The daily-cap-filled-mid-compose backlog stash strips a malicious body via the real nh3 sanitizer."""
    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True))[1],
    )

    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com/",
        payload={"txid": "", "round_num": 0},
    )
    composed = ArticleComposeResult(
        title="T", summary="s", body=_MALICIOUS_BODY, composer="deepseek"
    )

    out = pt._stash_capped_compose_to_backlog(
        row=row,
        composed=composed,
        payload=row.payload,
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.GENERIC,
        reason="standard daily publish cap reached (3/3)",
    )

    assert out["status"] == "approved_backlog"
    _assert_sanitized(stored["body"])


def _signals() -> object:
    from app.modules.ai.content_signals import ContentSignals

    return ContentSignals(
        category="ecosystem",
        categories=("ecosystem",),
        relevance=0.9,
        publish_decision=None,
        confidence=0.5,
        storage_score=0.9,
    )


def test_hold_for_review_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review-hold write path strips a malicious body via the real nh3 sanitizer."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: False,
    )
    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True))[1],
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review",
        lambda **_kw: "rid-1",
    )
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, None, True))
    _wire_domain_tracker(monkeypatch)

    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com/gallery",
    )
    composed = ArticleComposeResult(
        title="T", summary="s", body=_MALICIOUS_BODY, composer="deepseek"
    )

    out = pt._hold_for_review(
        row,
        {"txid": "", "round_num": 0},
        composed,
        topic=PublishTopic.GENERIC,
        publish_kind=PublishKind.CONTENT_UPDATE,
        compose_domain="example.com",
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
    _assert_sanitized(stored["body"])


def test_finalize_publish_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The straight-to-feed publish path strips a malicious body via the real nh3 sanitizer."""
    stored: dict = {}

    def _fake_insert_article(**kw: object) -> str:
        stored.update(kw)
        return "dddddddd-dddd-dddd-dddd-dddddddddddd"

    monkeypatch.setattr(pt, "insert_article", _fake_insert_article)
    monkeypatch.setattr(pt.index_article, "delay", lambda **_kw: None)
    monkeypatch.setattr(pt, "enqueue_article_translations", lambda _article_id: None)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.release_publish_slot", lambda **_kw: None
    )
    _wire_domain_tracker(monkeypatch)

    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com/",
    )
    composed = ArticleComposeResult(
        title="T", summary="s", body=_MALICIOUS_BODY, composer="deepseek"
    )

    out = pt._finalize_publish(
        row,
        {"txid": "", "round_num": 0},
        composed,
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.GENERIC,
        tier=PublishTier.STANDARD,
        compose_domain="",
    )

    assert out["status"] == "published"
    _assert_sanitized(stored["body"])


def _fake_review_row() -> SimpleNamespace:
    return SimpleNamespace(
        review_id=UUID("11111111-1111-1111-1111-111111111111"),
        url="https://example.com/svc",
        page_text="page text",
        page_title="Page title",
        category="ecosystem",
        storage_score=0.9,
        status="pending",
        created_at=None,
        metadata={},
    )


def test_recompose_review_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recompose_review draft-store write path strips a malicious body via the real nh3 sanitizer."""

    class _FakeSession:
        def execute(self, *_a: object, **_kw: object) -> _FakeSession:
            return self

        def one(self) -> SimpleNamespace:
            return _fake_review_row()

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review",
        lambda **_kw: "rid-2",
    )

    composed = ArticleComposeResult(
        title="T", summary="s", body=_MALICIOUS_BODY, composer="deepseek"
    )
    monkeypatch.setattr(pt, "_recompose_via_writer", lambda **_kw: (composed, None))
    monkeypatch.setattr(pt, "_recompose_resolve_image", lambda *_a, **_kw: ("", ""))
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, None, True))

    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", False))[1],
    )

    out = pt.recompose_review.run("11111111-1111-1111-1111-111111111111")

    assert out["status"] == "ok"
    _assert_sanitized(stored["body"])


def test_recompose_published_sanitizes_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recompose_published archive-refresh draft-store write path strips a malicious body via the real nh3 sanitizer."""
    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/svc",
        body="live page body",
        title="old title",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )
    monkeypatch.setattr("app.core.config.SERVICE_CONTEXT_ENABLED", False)

    composed = ArticleComposeResult(
        title="T", summary="s", body=_MALICIOUS_BODY, composer="deepseek"
    )
    monkeypatch.setattr(pt, "compose_scrape_article", lambda **_kw: composed)

    class _FakeSession:
        def execute(self, *_a: object, **_kw: object) -> _FakeSession:
            return self

        def one(self) -> None:
            return None

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)

    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("dddddddd-dddd-dddd-dddd-dddddddddddd", False))[1],
    )
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, None, True))
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review",
        lambda **_kw: "rid-99",
    )

    out = pt.recompose_published.run("22222222-2222-2222-2222-222222222222")

    assert out["status"] == "ok"
    _assert_sanitized(stored["body"])
