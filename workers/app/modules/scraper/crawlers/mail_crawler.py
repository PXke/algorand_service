"""CrawlerDriver implementation for the mail inbox source."""

from __future__ import annotations

from app.modules.scraper.core.mail_scraper import fetch_unread_messages
from app.modules.scraper.crawler_types import CrawlerType


class MailCrawlerDriver:
    """IMAP lane — not URL-based; used by poll_mail_inbox."""

    crawler_type = CrawlerType.MAIL.value

    def poll_inbox(self, *, limit: int = 15) -> list[dict[str, str]]:
        """Fetch recent unread messages from the configured IMAP inbox."""
        return fetch_unread_messages(limit=limit)
