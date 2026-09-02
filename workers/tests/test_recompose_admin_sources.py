"""Owner-supplied article sources wired into recompose_review / recompose_published (docs/newspaper-article-sources-design.md, Phase 1).

Covers: sources loaded before any LLM spend and forwarded into
compose_scrape_article; the existing LLM-failure re-enqueue path is intact
when sources are attached; and the fail-CLOSED admin-sources read-error path
on both recompose entry points (CLAUDE.md invariant #8 -- an error must
never look like "no sources", and neither path may spend on an LLM call
before the load succeeds).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Never

import pytest

from app.modules.newspaper.admin_source_store import AdminSource
from app.modules.newspaper.tasks import publish_tasks as pt

_SOURCE = AdminSource(
    source_id="s1",
    added_by="0xADMIN",
    label="Exclusive interview",
    kind="text",
    attribution_url="",
    content="the creator said...",
    added_at=None,
)


def _fake_review_row(
    *, article_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
) -> SimpleNamespace:
    import json

    return SimpleNamespace(
        review_id="11111111-1111-1111-1111-111111111111",
        url="https://example.com/svc",
        page_text="page text",
        page_title="Page title",
        category="ecosystem",
        storage_score=0.9,
        status="pending",
        created_at=None,
        metadata={"raw": json.dumps({"article_id": article_id, "service_id": "svc"})},
    )


def _noop_prior_search_x(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op the (unrelated) prior-search_x-findings reinjection both recompose paths also do -- not this feature's concern, just needs to not blow up the fake Cassandra session wired for the admin-sources assertions below."""
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_prior_search_x_findings",
        lambda *_a, **_kw: [],
    )


def _wire_review_session(monkeypatch: pytest.MonkeyPatch, row: SimpleNamespace) -> None:
    class _FakeSession:
        def execute(self, *_a: object, **_kw: object) -> _FakeSession:
            return self

        def one(self) -> SimpleNamespace:
            return row

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)


# --------------------------------------------------------------------------- #
# recompose_review: sources loaded and forwarded
# --------------------------------------------------------------------------- #


def test_recompose_review_loads_sources_by_the_old_article_id_and_forwards_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sources are loaded keyed on the review's stored article_id (design doc section 2.2) and forwarded into compose_scrape_article's admin_sources kwarg."""
    row = _fake_review_row(article_id="cccccccc-cccc-cccc-cccc-cccccccccccc")
    _wire_review_session(monkeypatch, row)
    _noop_prior_search_x(monkeypatch)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        lambda *_a, **_kw: None,
    )

    captured_load: list[str] = []

    def _fake_load(article_id: str, **_kw: object) -> list[AdminSource]:
        captured_load.append(article_id)
        return [_SOURCE]

    monkeypatch.setattr("app.modules.newspaper.admin_source_store.load_active_sources", _fake_load)

    captured_compose: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured_compose.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_review.run("11111111-1111-1111-1111-111111111111")

    assert captured_load == ["cccccccc-cccc-cccc-cccc-cccccccccccc"]
    assert captured_compose["admin_sources"] == [_SOURCE]


def test_recompose_review_llm_failure_still_reenqueues_with_sources_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feature must not break the existing failure path: an LLM failure during compose still restores the original proposal via enqueue_classifier_review, even when admin_sources were successfully loaded and passed in."""
    from app.modules.ai.llm_provider import LLMError

    row = _fake_review_row()
    _wire_review_session(monkeypatch, row)
    _noop_prior_search_x(monkeypatch)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.admin_source_store.load_active_sources",
        lambda *_a, **_kw: [_SOURCE],
    )

    def _boom_compose(**_kw: object) -> Never:
        raise LLMError("provider unavailable")

    monkeypatch.setattr(pt, "compose_scrape_article", _boom_compose)

    captured_enqueue: dict = {}

    def _fake_enqueue(**kw: object) -> str:
        captured_enqueue.update(kw)
        return "rid-restored"

    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review", _fake_enqueue
    )

    result = pt.recompose_review.run("11111111-1111-1111-1111-111111111111")

    assert result["status"] == "mistral_failed"
    assert "recompose_failed" in captured_enqueue["metadata"]
    assert captured_enqueue["metadata"]["article_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


# --------------------------------------------------------------------------- #
# Fail-closed: admin-sources read error, BEFORE any LLM spend
# --------------------------------------------------------------------------- #


def test_recompose_review_fails_closed_on_admin_sources_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra read error loading admin sources must produce the explicit unavailable status, re-enqueue the review (same mechanics as the LLM-failure branch), and NEVER call compose_scrape_article -- nothing may be spent on an LLM call for a compose the owner's evidence never reached."""
    row = _fake_review_row()
    _wire_review_session(monkeypatch, row)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        lambda *_a, **_kw: None,
    )

    def _boom_load(*_a: object, **_kw: object) -> Never:
        raise ConnectionError("cassandra down")

    monkeypatch.setattr("app.modules.newspaper.admin_source_store.load_active_sources", _boom_load)

    def _fail_if_called(**_kw: object) -> Never:
        raise AssertionError("must not spend on an LLM call when admin sources failed to load")

    monkeypatch.setattr(pt, "compose_scrape_article", _fail_if_called)

    captured_enqueue: dict = {}

    def _fake_enqueue(**kw: object) -> str:
        captured_enqueue.update(kw)
        return "rid-restored"

    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review", _fake_enqueue
    )

    result = pt.recompose_review.run("11111111-1111-1111-1111-111111111111")

    assert result["status"] == "admin_sources_unavailable"
    assert "admin_sources_unavailable" in captured_enqueue["metadata"]["recompose_failed"]
    assert captured_enqueue["metadata"]["article_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_recompose_published_fails_closed_on_admin_sources_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fail-CLOSED contract on the live-article recompose path: a read error returns the explicit unavailable status without ever calling compose_scrape_article, and (per section 4 of the design doc) the live article is left completely untouched -- nothing to re-enqueue since no review/draft exists yet at this point."""
    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="live page body",
        title="t",
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

    captured_load: list[str] = []

    def _boom_load(article_id: str, **_kw: object) -> Never:
        captured_load.append(article_id)
        raise ConnectionError("cassandra down")

    monkeypatch.setattr("app.modules.newspaper.admin_source_store.load_active_sources", _boom_load)

    def _fail_if_called(**_kw: object) -> Never:
        raise AssertionError("must not spend on an LLM call when admin sources failed to load")

    monkeypatch.setattr(pt, "compose_scrape_article", _fail_if_called)

    def _boom_insert(**_kw: object) -> Never:
        raise AssertionError("must not write anything -- the live article stays untouched")

    monkeypatch.setattr("app.modules.newspaper.article_store.insert_stored_article", _boom_insert)

    result = pt.recompose_published.run("22222222-2222-2222-2222-222222222222")

    assert result["status"] == "admin_sources_unavailable"
    assert captured_load == ["22222222-2222-2222-2222-222222222222"]


def test_recompose_published_forwards_loaded_sources_into_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: sources loaded by the LIVE article_id (design doc section 2.2) reach compose_scrape_article's admin_sources kwarg on the generic (non-editorial-brief) path."""
    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="live page body",
        title="t",
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
    _noop_prior_search_x(monkeypatch)
    monkeypatch.setattr(
        "app.modules.newspaper.admin_source_store.load_active_sources",
        lambda *_a, **_kw: [_SOURCE],
    )

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_published.run("33333333-3333-3333-3333-333333333333")

    assert captured["admin_sources"] == [_SOURCE]
    assert captured["publish_topic"] == pt.PublishTopic.GENERIC
