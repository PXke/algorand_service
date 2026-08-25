"""IMAP-backed mail inbox scraper."""

from __future__ import annotations

import contextlib
import email
import hashlib
import imaplib
import logging
from collections.abc import Callable
from email.header import decode_header

from app.core.config import (
    MAIL_IMAP_FOLDER,
    MAIL_IMAP_HOST,
    MAIL_IMAP_PASSWORD,
    MAIL_IMAP_PORT,
    MAIL_IMAP_USER,
)
from app.modules.scraper.core.base import ScrapeResult

logger = logging.getLogger(__name__)


class MailScraperError(Exception):
    """Raised when the mail inbox can't be read."""

    pass


def decode_mime_header(value: str | None) -> str:
    """Decode an RFC 2047 MIME-encoded email header into a plain string."""
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


def poll_unread_messages(
    *,
    limit: int = 20,
    on_message: Callable[[dict[str, str]], bool],
) -> list[dict[str, str]]:
    r"""Fetch recent UNSEEN messages one at a time and hand each to `on_message`.

    Uses ``BODY.PEEK[]`` rather than ``RFC822`` to fetch a message's body --
    per IMAP spec, fetching the full ``RFC822`` form implicitly sets the
    server-side ``\\Seen`` flag the instant it's fetched, regardless of
    whether the message is ever actually processed. ``BODY.PEEK[]`` fetches
    the identical content without that side effect, so a message is only
    marked ``\\Seen`` (via an explicit ``STORE``) after ``on_message`` returns
    ``True`` for it. A message ``on_message`` returns ``False`` for -- or
    that raises out of ``on_message`` entirely -- is left unseen, so it's
    picked up again on the next poll instead of being silently lost.

    ``on_message`` is called once per fetched message with
    ``{"uid", "from", "subject", "text"}`` and should return whether it was
    processed successfully. If it raises, the exception is caught here so
    the rest of the batch still gets attempted (callers that want finer
    control over their own error handling/logging should catch inside
    ``on_message`` and return ``False`` instead of letting it raise).
    """
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
            message = _fetch_message(client, uid)
            if message is None:
                continue
            messages.append(message)
            try:
                success = on_message(message)
            except Exception:
                logger.exception(
                    "poll_unread_messages: on_message raised for uid=%s; leaving unseen",
                    message["uid"],
                )
                continue
            if success:
                client.store(uid, "+FLAGS", "\\Seen")
        return messages
    finally:
        with contextlib.suppress(Exception):
            client.logout()


def _fetch_message(client: imaplib.IMAP4_SSL, uid: bytes) -> dict[str, str] | None:
    r"""Fetch and parse one message by UID via BODY.PEEK[] (does not mark it \\Seen)."""
    _st, fetched = client.fetch(uid, "(BODY.PEEK[])")
    if not fetched or not fetched[0]:
        return None
    raw = fetched[0][1]
    msg = email.message_from_bytes(raw)
    subject = decode_mime_header(msg.get("Subject"))
    from_hdr = decode_mime_header(msg.get("From"))
    body = _extract_body(msg)
    text = f"From: {from_hdr}\nSubject: {subject}\n\n{body}"
    return {
        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
        "from": from_hdr,
        "subject": subject,
        "text": text,
    }


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
    """Build the ScrapeResult for a single fetched mail message."""
    url = f"mail://message/{uid}"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ScrapeResult(
        source_id=service_id,
        url=url,
        title=subject or "Email",
        text=text,
        content_hash=content_hash,
    )
