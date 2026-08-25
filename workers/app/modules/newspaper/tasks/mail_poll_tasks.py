"""Celery task that polls the mail inbox source."""

from __future__ import annotations

import logging
import uuid

from app.celery_app import celery_app
from app.core.config import (
    MAIL_IMAP_HOST,
    MAIL_NEWS_DISPLAY_NAME,
    MAIL_NEWS_SERVICE_ID,
)
from app.modules.newspaper.ingest_signal import ingest_publish_signal
from app.modules.scraper.core.mail_scraper import mail_message_result, poll_unread_messages
from app.modules.scraper.crawler_registry import mail_crawl_disabled_reason

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.newspaper.poll_mail_inbox")
def poll_mail_inbox() -> dict[str, object]:
    r"""Poll IMAP for unseen messages and enqueue official comms.

    Each message is only marked ``\\Seen`` (by mail_scraper.poll_unread_messages,
    after `_process` below returns True) once it's been successfully ingested --
    a message whose `ingest_publish_signal` call raises stays unseen so it's
    retried on the next poll instead of being lost, and the try/except in
    `_process` means one message's failure doesn't stop the rest of the batch
    from being attempted.
    """
    mail_off = mail_crawl_disabled_reason()
    if mail_off:
        return {"status": "skipped", "reason": mail_off, "polled": 0}
    if not MAIL_IMAP_HOST:
        return {"status": "skipped", "reason": "MAIL_IMAP_HOST unset", "polled": 0}

    results: list[dict[str, str]] = []

    def _process(msg: dict[str, str]) -> bool:
        uid = msg["uid"]
        try:
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
        except Exception:
            logger.exception(
                "poll_mail_inbox: failed to ingest mail uid=%s; leaving unseen for retry", uid
            )
            results.append({"uid": uid, "status": "error"})
            return False
        results.append({"uid": uid, **outcome})
        return True

    messages = poll_unread_messages(limit=15, on_message=_process)
    return {"status": "ok", "polled": len(messages), "results": results}
