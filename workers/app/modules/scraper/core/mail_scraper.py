from __future__ import annotations

import contextlib
import email
import hashlib
import imaplib
from email.header import decode_header

from app.core.config import (
    MAIL_IMAP_FOLDER,
    MAIL_IMAP_HOST,
    MAIL_IMAP_PASSWORD,
    MAIL_IMAP_PORT,
    MAIL_IMAP_USER,
)
from app.modules.scraper.core.base import BaseScraper, ScrapeResult


class MailScraperError(Exception):
    pass


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out: list[str] = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out).strip()


class MailMessageScraper(BaseScraper):
    """Scrape a single mail://message/{uid} item (body fetched via IMAP in poll task)."""

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        msg = "MailScraper requires pre-fetched body; use mail poll task"
        raise MailScraperError(msg)


def fetch_unread_messages(*, limit: int = 20) -> list[dict[str, str]]:
    """Fetch recent UNSEEN messages from configured IMAP mailbox."""
    if not MAIL_IMAP_HOST or not MAIL_IMAP_USER:
        return []

    client = imaplib.IMAP4_SSL(MAIL_IMAP_HOST, MAIL_IMAP_PORT)
    try:
        client.login(MAIL_IMAP_USER, MAIL_IMAP_PASSWORD)
        client.select(MAIL_IMAP_FOLDER)
        _status, data = client.search(None, "UNSEEN")
        if not data or not data[0]:
            return []
        uids = data[0].split()[-limit:]
        messages: list[dict[str, str]] = []
        for uid in uids:
            _st, fetched = client.fetch(uid, "(RFC822)")
            if not fetched or not fetched[0]:
                continue
            raw = fetched[0][1]
            msg = email.message_from_bytes(raw)
            subject = decode_mime_header(msg.get("Subject"))
            from_hdr = decode_mime_header(msg.get("From"))
            body = _extract_body(msg)
            text = f"From: {from_hdr}\nSubject: {subject}\n\n{body}"
            messages.append(
                {
                    "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "from": from_hdr,
                    "subject": subject,
                    "text": text,
                }
            )
        return messages
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def mail_message_result(
    *,
    service_id: str,
    uid: str,
    subject: str,
    text: str,
) -> ScrapeResult:
    url = f"mail://message/{uid}"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ScrapeResult(
        source_id=service_id,
        url=url,
        title=subject or "Email",
        text=text,
        content_hash=content_hash,
    )
