"""IndexNow pings for admin paths that change a public URL.

Mirror of workers/app/modules/newspaper/indexnow.py (the two apps don't share
code): one POST to api.indexnow.org fans out to all IndexNow participants.
Bing's guidelines ask for ADD, UPDATE, and REMOVE — workers cover auto-publish
and edits; this covers admin approve-to-feed, patch, and delete.
Best-effort: never block or fail an admin action over this.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.indexnow.org/indexnow"
_MAX_URLS_PER_REQUEST = 10_000
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = 0.5


def _site_url() -> str:
    return settings.public_site_url.rstrip("/")


def article_url(article_id: str, lang: str | None = None) -> str:
    """Absolute article URL; non-English locales use ?lang= (matches SSR/sitemap)."""
    base = f"{_site_url()}/news/articles/{article_id}"
    code = (lang or "").strip()
    if code and code != "en":
        return f"{base}?lang={code}"
    return base


def translation_lang_codes(translations: dict[str, str] | None) -> list[str]:
    """Return the non-English language codes present in an article's translations map."""
    if not translations:
        return []
    return [lang for lang in translations if lang and lang != "en"]


def article_urls(article_id: str, translation_langs: Iterable[str] | None = None) -> list[str]:
    """Return the deduped English + translation URLs for an article."""
    langs: list[str] = []
    seen: set[str] = set()
    for lang in translation_langs or ():
        code = (lang or "").strip()
        if not code or code == "en" or code in seen:
            continue
        seen.add(code)
        langs.append(code)
    urls = [article_url(article_id)]
    urls.extend(article_url(article_id, lang) for lang in langs)
    return _dedupe_urls(urls)


def sitemap_url() -> str:
    """Return the absolute URL of the main sitemap."""
    return f"{_site_url()}/sitemap.xml"


def sitemap_news_url() -> str:
    """Return the absolute URL of the Google News sitemap."""
    return f"{_site_url()}/sitemap-news.xml"


def content_change_urls(
    article_id: str,
    *,
    translation_langs: Iterable[str] | None = None,
) -> list[str]:
    """Article URL(s) plus sitemaps — use on publish, edit, or delete."""
    urls = article_urls(article_id, translation_langs)
    urls.extend((sitemap_url(), sitemap_news_url()))
    return _dedupe_urls(urls)


def translation_change_urls(article_id: str, lang: str) -> list[str]:
    """New or updated locale URL plus sitemaps — use when a translation lands."""
    urls = [article_url(article_id, lang), sitemap_url(), sitemap_news_url()]
    return _dedupe_urls(urls)


def ping_article(
    article_id: str,
    *,
    translation_langs: Iterable[str] | None = None,
) -> None:
    """Ping IndexNow for an article's URL(s) after a publish, edit, or delete."""
    ping(content_change_urls(article_id, translation_langs=translation_langs))


def ping_translation(article_id: str, lang: str) -> None:
    """Ping IndexNow for a newly landed translation's URL."""
    ping(translation_change_urls(article_id, lang))


def _dedupe_urls(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        u = (url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def ping(urls: list[str] | Iterable[str]) -> None:
    """Notify IndexNow of added/updated/removed URLs. No-op without key/URLs."""
    key = (settings.indexnow_key or "").strip()
    urls = _dedupe_urls(urls)
    if not key or not urls:
        return
    host = settings.public_site_url.split("://", 1)[-1].split("/", 1)[0]
    key_location = f"{_site_url()}/{key}.txt"
    for start in range(0, len(urls), _MAX_URLS_PER_REQUEST):
        chunk = urls[start : start + _MAX_URLS_PER_REQUEST]
        payload = {
            "host": host,
            "key": key,
            "keyLocation": key_location,
            "urlList": chunk,
        }
        _post_with_retry(payload, chunk)


def _post_with_retry(payload: dict, urls_for_log: list[str]) -> None:
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = httpx.post(_ENDPOINT, json=payload, timeout=8.0)
            if resp.status_code < 400:
                log.info("indexnow ping %s -> %s", urls_for_log, resp.status_code)
                return
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            log.warning(
                "indexnow ping %s -> HTTP %s (verify key file at %s)",
                urls_for_log,
                resp.status_code,
                payload.get("keyLocation"),
            )
            return
        except Exception as exc:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            log.warning(
                "indexnow ping failed after %s attempts (%s): %s",
                _MAX_ATTEMPTS,
                urls_for_log,
                exc,
            )
