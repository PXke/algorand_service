from __future__ import annotations

import uuid

from app.celery_app import celery_app
from app.core.config import (
    MAIL_IMAP_HOST,
    MAIL_NEWS_DISPLAY_NAME,
    MAIL_NEWS_SERVICE_ID,
)
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.scraper.core.mail_scraper import fetch_unread_messages, mail_message_result
from app.modules.scraper.crawler_registry import mail_crawl_disabled_reason


@celery_app.task(name="app.tasks.newspaper.poll_mail_inbox")
def poll_mail_inbox() -> dict[str, object]:
    """Poll IMAP for unseen messages and enqueue official comms."""
    mail_off = mail_crawl_disabled_reason()
    if mail_off:
        return {"status": "skipped", "reason": mail_off, "polled": 0}
    if not MAIL_IMAP_HOST:
        return {"status": "skipped", "reason": "MAIL_IMAP_HOST unset", "polled": 0}

    messages = fetch_unread_messages(limit=15)
    results: list[dict[str, str]] = []
    for msg in messages:
        uid = msg["uid"]
        result = mail_message_result(
            service_id=MAIL_NEWS_SERVICE_ID,
            uid=uid,
            subject=msg.get("subject", ""),
            text=msg.get("text", ""),
        )
        trigger_id = f"mail-poll-{uuid.uuid4().hex[:16]}"
        outcome = ingest_publish_signal(
            service_id=MAIL_NEWS_SERVICE_ID,
            display_name=MAIL_NEWS_DISPLAY_NAME,
            source_url=result.url,
            page_title=result.title,
            page_text=result.text,
            source_kind="mail",
            match_kind="mail",
            match_value=msg.get("from", ""),
            txid=trigger_id,
            mail_from=msg.get("from", ""),
        )
        results.append({"uid": uid, **outcome})

    return {"status": "ok", "polled": len(messages), "results": results}
