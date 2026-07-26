"""IndexNow: push article URL changes to participating search engines.

Thin adapter over `algorand_shared.indexnow`, shared with the backend (which
pings on admin approve/patch/delete). Only the configuration source differs —
this side reads module-level constants, the backend reads msgspec `settings`.

Workers ping on auto-publish, the paced feed release, article edits and newly
landed translations. Best-effort: never block or fail a publish over a ping.
"""

from __future__ import annotations

from algorand_shared.indexnow import IndexNowClient, translation_lang_codes

from app.core import config

__all__ = [
    "article_url",
    "article_urls",
    "ping",
    "ping_article",
    "ping_translation",
    "translation_lang_codes",
]

_client = IndexNowClient(
    site_url=lambda: config.PUBLIC_SITE_URL,
    key=lambda: config.INDEXNOW_KEY or "",
)

article_url = _client.article_url
article_urls = _client.article_urls
content_change_urls = _client.content_change_urls
translation_change_urls = _client.translation_change_urls
ping = _client.ping
ping_article = _client.ping_article
ping_translation = _client.ping_translation
