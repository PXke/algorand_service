"""IndexNow pings for admin actions that change a public URL.

Thin adapter over `algorand_shared.indexnow`: the logic is shared with the
workers (which ping on publish/edit/translation), and only the configuration
source differs — this side reads msgspec `settings`, they read module-level
constants. That single difference is what used to justify a whole forked copy.

Bing's guidelines ask for ADD, UPDATE and REMOVE; workers cover auto-publish
and edits, this covers admin approve-to-feed, patch and delete. Best-effort:
never block or fail an admin action over a ping.
"""

from __future__ import annotations

from algorand_shared.indexnow import IndexNowClient, translation_lang_codes

from app.core.config import settings

__all__ = ["article_url", "ping", "ping_article", "translation_lang_codes"]

_client = IndexNowClient(
    site_url=lambda: settings.public_site_url,
    key=lambda: settings.indexnow_key or "",
)

article_url = _client.article_url
article_urls = _client.article_urls
content_change_urls = _client.content_change_urls
ping = _client.ping
ping_article = _client.ping_article
