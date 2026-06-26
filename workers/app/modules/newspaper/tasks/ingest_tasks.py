from __future__ import annotations

import uuid

from app.celery_app import celery_app
from app.modules.newspaper.external_ingest_queue import pop_external_signal, queue_depth
from app.modules.newspaper.ingest_signal import ingest_publish_signal


def _run_ingest_payload(payload: dict) -> dict[str, str]:
    crawl_url = str(payload.get("url", "")).strip()
    if crawl_url and crawl_url.startswith(("http://", "https://")):
        from app.modules.crawler.url_queue import enqueue_url

        queue_id, created = enqueue_url(crawl_url, source="push", priority=60)
        if created or queue_id:
            return {"status": "url_enqueued", "queue_id": queue_id}

    service_id = str(payload.get("service_id", "")).strip()
    page_text = str(payload.get("page_text", "")).strip()
    if not service_id or not page_text:
        return {"status": "skipped", "reason": "missing service_id or page_text"}

    source_url = str(payload.get("source_url", "")).strip() or f"push://{service_id}"
    return ingest_publish_signal(
        service_id=service_id,
        display_name=str(payload.get("display_name", service_id)).strip(),
        source_url=source_url,
        page_title=str(payload.get("page_title", "Announcement")).strip(),
        page_text=page_text,
        source_kind=str(payload.get("source_kind", "push")).strip() or "push",
        match_kind=str(payload.get("match_kind", "push")).strip(),
        match_value=str(payload.get("match_value", "")).strip(),
        txid=str(payload.get("txid", f"push-{uuid.uuid4().hex[:16]}")),
        round_num=int(payload.get("round_num", 0)),
        mail_from=str(payload.get("mail_from", "")).strip(),
        transcript_text=str(payload.get("transcript_text", "")).strip(),
        publish_mode=str(payload.get("publish_mode", "")).strip(),
        linked_article_id=str(payload.get("linked_article_id", "")).strip(),
    )


@celery_app.task(name="app.tasks.ingest.ingest_external_signal")
def ingest_external_signal(**payload: object) -> dict[str, str]:
    """Process one push payload (direct call or from Redis queue)."""
    if not isinstance(payload, dict):
        return {"status": "skipped", "reason": "invalid_payload"}
    return _run_ingest_payload(payload)


@celery_app.task(name="app.tasks.ingest.drain_external_ingest_queue")
def drain_external_ingest_queue(*, max_items: int = 20) -> dict[str, object]:
    """Drain Redis push queue into publish pipeline."""
    results: list[dict[str, str]] = []
    processed = 0
    for _ in range(max_items):
        payload = pop_external_signal()
        if payload is None:
            break
        outcome = _run_ingest_payload(payload)
        results.append(outcome)
        processed += 1

    return {
        "status": "ok",
        "processed": processed,
        "remaining": queue_depth(),
        "results": results,
    }
