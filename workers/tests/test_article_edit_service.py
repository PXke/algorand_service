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
        "app.modules.ai.mistral_compose.compose_article_edit_mistral",
        lambda **_kw: SimpleNamespace(title="T", summary="S", body="B"),
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
