from __future__ import annotations

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


def test_run_article_edit_skips_cleanly_when_mistral_not_configured(monkeypatch) -> None:
    """No template fallback exists (owner decision 2026-07-14) — a Mistral-
    unconfigured edit attempt must return a clean skip status, matching the
    existing MistralError handling already in run_article_edit, not raise
    an uncaught exception or silently produce a template-authored edit."""
    import app.modules.newspaper.article_edit_service as svc

    monkeypatch.setattr(
        svc,
        "get_article",
        lambda article_id: ArticleDetail(
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
