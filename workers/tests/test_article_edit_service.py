"""In-place article edits skip cleanly when Mistral is off and re-anchor match keys."""

from __future__ import annotations

import pytest

from app.modules.newspaper.article_store import ArticleDetail
from app.modules.newspaper.publish_queue_store import QueuedPublishRow


def _row() -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id="q1",
        priority=5,
        topic="content_update",
        publish_kind="content_update",
        service_id="svc",
        display_name="Svc",
        scrape_url="https://example.com/",
        payload={"linked_article_id": "a1", "page_text": "new text", "page_title": "New"},
        created_at_epoch=0,
    )


def test_run_article_edit_skips_cleanly_when_mistral_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No template fallback exists (owner decision 2026-07-14) — a Mistral- unconfigured edit attempt must return a clean skip status, matching the existing MistralError handling already in run_article_edit, not raise an uncaught exception or silently produce a template-authored edit."""
    import app.modules.newspaper.article_edit_service as svc

    monkeypatch.setattr(
        svc,
        "get_article",
        lambda _article_id: ArticleDetail(
            article_id="a1",
            service_id="svc",
            title="Existing title",
            summary="Existing summary",
            body="Existing body",
            published_at_epoch=1_700_000_000,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com/",
        ),
    )
    monkeypatch.setattr(svc, "mistral_configured", lambda: False)

    result = svc.run_article_edit(_row())
    assert result["status"] == "mistral_failed"
    assert result["linked_article_id"] == "a1"


def test_article_not_found_retires_queue_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit 2026-07-17: a deleted linked article is a PERMANENT condition, but the plain "skipped" status left the queue row pending — redrained every ~2-minute breaking beat forever and starving the service's one pending-row slot. queue_status lets _resolve retire the row."""
    import app.modules.newspaper.article_edit_service as svc

    monkeypatch.setattr(svc, "get_article", lambda _article_id: None)

    result = svc.run_article_edit(_row())
    assert result["status"] == "skipped"
    assert result["reason"] == "article_not_found"
    assert result["queue_status"] == "expired"


def test_edit_reregisters_match_keys_anchored_to_publish_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-07-17: re-registering match keys with the default closes_at (now + window) meant every edit rolled the article's edit window forward another 24h — and since each edit also adds the editing source's own keys, one stray match could keep an article editable (and accumulating keys) indefinitely. That rolling window is how the runaway loop kept re-opening itself. Keys must re-register anchored to the article's ORIGINAL publish time, converging with is_edit_window_open."""
    from datetime import UTC, datetime

    import app.modules.newspaper.article_edit_service as svc
    from app.modules.newspaper.article_matching import edit_window_closes_at

    published_epoch = 1_700_000_000
    monkeypatch.setattr(
        svc,
        "get_article",
        lambda _article_id: ArticleDetail(
            article_id="a1",
            service_id="svc",
            title="Existing title",
            summary="Existing summary",
            body="Existing body",
            published_at_epoch=published_epoch,
            trigger_txid="",
            trigger_round=0,
            source_url="https://example.com/",
        ),
    )
    monkeypatch.setattr(svc, "mistral_configured", lambda: True)
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.modules.newspaper.article_composer.compose_scrape_article",
        lambda **_kw: SimpleNamespace(
            title="T",
            summary="S",
            body="B",
            composer="mistral",
            extra_tags=(),
            defunct_domains=(),
            unsourced_hold_reason="",
            broken_link_hold_reason="",
        ),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.prior_service_article_summary",
        lambda _service_id: "",
    )
    monkeypatch.setattr(svc, "sanitize_body", lambda b: b)
    monkeypatch.setattr(svc, "save_article_version", lambda **_kw: 2)
    monkeypatch.setattr(svc, "derive_article_tags", lambda **_kw: ("algorand",))
    monkeypatch.setattr(svc, "update_article", lambda **_kw: True)
    monkeypatch.setattr(svc.index_article, "delay", lambda **_kw: None)
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", lambda *_a, **_k: None)
    registered: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.build_match_keys",
        lambda **_kw: [("service_id", "svc")],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.register_article_match_keys",
        lambda **kw: registered.update(kw) or 1,
    )

    result = svc.run_article_edit(_row())
    assert result["status"] == "edited"
    expected = edit_window_closes_at(from_time=datetime.fromtimestamp(published_epoch, tz=UTC))
    assert registered["closes_at"] == expected


def test_stale_edit_row_falls_through_to_create_when_window_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit 2026-07-17: publish_mode is decided at INGEST time, but a row can sit pending for days behind cooldowns (observed: 4-day-old edit row) and drain long after the linked article's edit window closed. The drain must re-check the window: closed -> compose as a NEW article (what resolve_publish_mode would decide today), never edit a days-old piece."""
    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    row = _row()
    row.payload["publish_mode"] = "edit"
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.is_edit_window_open",
        lambda _aid: False,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_edit_service.run_article_edit",
        lambda _row: pytest.fail("must not edit once the window is closed"),
    )

    class _CreatePathReachedError(Exception):
        pass

    monkeypatch.setattr(
        pt,
        "_compose_domain_for_row",
        lambda _row: (_ for _ in ()).throw(_CreatePathReachedError()),
    )

    with pytest.raises(_CreatePathReachedError):
        pt.publish_from_queued_row(row)
    # Payload mutated so downstream match-key registration (keyed on
    # publish_mode == "create") matches the path actually taken.
    assert row.payload["publish_mode"] == "create"
    assert "linked_article_id" not in row.payload


def test_open_window_edit_row_still_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs the edit path (not the create fallback) when the article's edit window is still open."""
    from app.modules.newspaper.tasks import publish_tasks as pt

    row = _row()
    row.payload["publish_mode"] = "edit"
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.is_edit_window_open",
        lambda _aid: True,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_edit_service.run_article_edit",
        lambda _row: {"status": "edited", "article_id": "a1"},
    )
    assert pt.publish_from_queued_row(row) == {"status": "edited", "article_id": "a1"}


def _existing_article() -> ArticleDetail:
    return ArticleDetail(
        article_id="a1",
        service_id="svc",
        title="Existing title",
        summary="Existing summary",
        body="Existing body -- this must never reach the recompose prompt",
        published_at_epoch=1_700_000_000,
        trigger_txid="",
        trigger_round=0,
        source_url="https://example.com/",
    )


def test_edit_recomposes_fully_without_leaking_the_old_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-04 (Humanitarian Network special edition): the old edit path fed the existing article's body back into the prompt and told the model to preserve it, which is why refreshes read as padded restatements. A full recompose must call the SAME router a fresh article uses (compose_scrape_article), with NO existing title/summary/body anywhere in the call -- first_coverage=False + a prior_coverage_block instead."""
    from types import SimpleNamespace

    import app.modules.newspaper.article_edit_service as svc

    monkeypatch.setattr(svc, "get_article", lambda _article_id: _existing_article())
    monkeypatch.setattr(svc, "mistral_configured", lambda: True)
    monkeypatch.setattr(svc, "sanitize_body", lambda b: b)
    monkeypatch.setattr(svc, "save_article_version", lambda **_kw: 2)
    monkeypatch.setattr(svc, "derive_article_tags", lambda **_kw: ["algorand"])
    monkeypatch.setattr(svc, "update_article", lambda **_kw: True)
    monkeypatch.setattr(svc.index_article, "delay", lambda **_kw: None)
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.build_match_keys",
        lambda **_kw: [("service_id", "svc")],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.register_article_match_keys",
        lambda **_kw: 1,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.prior_service_article_summary",
        lambda _service_id: "PRIOR: we already covered svc's launch.",
    )

    captured: dict = {}

    def _fake_compose(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            title="Fresh title",
            summary="Fresh summary",
            body="Fresh body",
            composer="mistral",
            extra_tags=("special-edition",),
            defunct_domains=(),
            unsourced_hold_reason="",
            broken_link_hold_reason="",
        )

    monkeypatch.setattr(
        "app.modules.newspaper.article_composer.compose_scrape_article", _fake_compose
    )

    result = svc.run_article_edit(_row())

    assert result["status"] == "edited"
    # The old body/title/summary must never appear anywhere in the compose call.
    serialized = str(captured)
    assert "Existing title" not in serialized
    assert "Existing summary" not in serialized
    assert "this must never reach the recompose prompt" not in serialized
    assert captured["first_coverage"] is False
    assert captured["prior_coverage_block"] == "PRIOR: we already covered svc's launch."
    assert captured["page_text"] == "new text"
    assert captured["page_title"] == "New"


def test_edit_merges_extra_tags_from_the_recompose(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag the recompose adds (e.g. "special-edition") survives into the final tag list even though derive_article_tags doesn't independently know about it."""
    from types import SimpleNamespace

    import app.modules.newspaper.article_edit_service as svc

    monkeypatch.setattr(svc, "get_article", lambda _article_id: _existing_article())
    monkeypatch.setattr(svc, "mistral_configured", lambda: True)
    monkeypatch.setattr(svc, "sanitize_body", lambda b: b)
    monkeypatch.setattr(svc, "save_article_version", lambda **_kw: 2)
    monkeypatch.setattr(svc, "derive_article_tags", lambda **_kw: ["algorand"])
    monkeypatch.setattr(svc.index_article, "delay", lambda **_kw: None)
    monkeypatch.setattr("app.modules.newspaper.indexnow.ping_article", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.build_match_keys",
        lambda **_kw: [("service_id", "svc")],
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.register_article_match_keys",
        lambda **_kw: 1,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.prior_service_article_summary",
        lambda _service_id: "",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_composer.compose_scrape_article",
        lambda **_kw: SimpleNamespace(
            title="T",
            summary="S",
            body="B",
            composer="mistral",
            extra_tags=("special-edition",),
            defunct_domains=(),
            unsourced_hold_reason="",
            broken_link_hold_reason="",
        ),
    )

    captured_tags: list = []

    def _fake_update(**kwargs: object) -> bool:
        captured_tags.extend(kwargs["tags"])
        return True

    monkeypatch.setattr(svc, "update_article", _fake_update)

    svc.run_article_edit(_row())

    assert captured_tags == ["algorand", "special-edition"]


def test_edit_handles_writer_abort_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full recompose can now genuinely call abort_article (real research, unlike the old preserve-and-append prompt which never earned that judgment) -- must resolve as a clean skip, not an uncaught crash."""
    import app.modules.newspaper.article_edit_service as svc
    from app.modules.ai.story_spike import StorySpikedError

    monkeypatch.setattr(svc, "get_article", lambda _article_id: _existing_article())
    monkeypatch.setattr(svc, "mistral_configured", lambda: True)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.prior_service_article_summary",
        lambda _service_id: "",
    )

    def _boom(**_kw: object) -> None:
        raise StorySpikedError(category="dead_project", reason="site is defunct")

    monkeypatch.setattr("app.modules.newspaper.article_composer.compose_scrape_article", _boom)

    result = svc.run_article_edit(_row())

    assert result["status"] == "aborted_by_writer"
    assert result["linked_article_id"] == "a1"
    assert "dead_project" in result["reason"]
