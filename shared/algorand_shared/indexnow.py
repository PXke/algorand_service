"""IndexNow: push URL changes to participating search engines.

One POST to api.indexnow.org fans out to Bing, Yandex, Seznam, Naver, Yep,
Amazonbot and the Internet Archive. Best-effort throughout: a ping must never
block or fail the publish/edit/delete that triggered it. The key is public (it
is also served as ``{key}.txt`` at the site root, which is how the receiving
engines verify ownership).

Both deployables ping: workers on publish/edit/translation, the backend on the
admin approve/patch/delete actions. Configuration is injected rather than read
here, because each side stores it differently (msgspec ``settings`` attributes
vs module-level constants) — that mismatch is the only thing that made the two
copies of this file look different, and the reason they drifted.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable

import httpx

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.indexnow.org/indexnow"
_MAX_URLS_PER_REQUEST = 10_000
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SEC = 0.5


def dedupe_urls(urls: Iterable[str]) -> list[str]:
    """Strip blanks and duplicates, preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        u = (url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def translation_lang_codes(translations: dict[str, str] | None) -> list[str]:
    """Return the non-English language codes present in an article's translations map."""
    if not translations:
        return []
    return [lang for lang in translations if lang and lang != "en"]


class IndexNowClient:
    """Builds article/sitemap URLs and pings IndexNow for one site.

    `site_url` and `key` are callables, read at call time rather than captured,
    so a test (or a settings reload) that changes the underlying configuration
    takes effect without rebuilding the client.
    """

    def __init__(self, *, site_url: Callable[[], str], key: Callable[[], str]) -> None:
        """Take config accessors, not values, so each service can supply its own storage."""
        self._site_url = site_url
        self._key = key

    # ── URL construction ────────────────────────────────────────────────────
    def site(self) -> str:
        """Public site base URL with no trailing slash."""
        return (self._site_url() or "").rstrip("/")

    def article_url(self, article_id: str, lang: str | None = None) -> str:
        """Absolute article URL; non-English locales use ?lang= (matches SSR/sitemap)."""
        base = f"{self.site()}/news/articles/{article_id}"
        code = (lang or "").strip()
        if code and code != "en":
            return f"{base}?lang={code}"
        return base

    def article_urls(
        self, article_id: str, translation_langs: Iterable[str] | None = None
    ) -> list[str]:
        """Deduped English URL plus each translated-locale URL for an article."""
        langs: list[str] = []
        seen: set[str] = set()
        for lang in translation_langs or ():
            code = (lang or "").strip()
            if not code or code == "en" or code in seen:
                continue
            seen.add(code)
            langs.append(code)
        urls = [self.article_url(article_id)]
        urls.extend(self.article_url(article_id, lang) for lang in langs)
        return dedupe_urls(urls)

    def sitemap_url(self) -> str:
        """Absolute URL of the main sitemap."""
        return f"{self.site()}/sitemap.xml"

    def sitemap_news_url(self) -> str:
        """Absolute URL of the Google News sitemap."""
        return f"{self.site()}/sitemap-news.xml"

    def content_change_urls(
        self, article_id: str, *, translation_langs: Iterable[str] | None = None
    ) -> list[str]:
        """Article URL(s) plus sitemaps — use on publish, edit, or delete."""
        urls = self.article_urls(article_id, translation_langs)
        urls.extend((self.sitemap_url(), self.sitemap_news_url()))
        return dedupe_urls(urls)

    def translation_change_urls(self, article_id: str, lang: str) -> list[str]:
        """New or updated locale URL plus sitemaps — use when a translation lands."""
        return dedupe_urls(
            [self.article_url(article_id, lang), self.sitemap_url(), self.sitemap_news_url()]
        )

    # ── Pinging ─────────────────────────────────────────────────────────────
    def ping_article(
        self, article_id: str, *, translation_langs: Iterable[str] | None = None
    ) -> None:
        """Ping IndexNow for an article's content change (publish, edit, or delete)."""
        self.ping(self.content_change_urls(article_id, translation_langs=translation_langs))

    def ping_translation(self, article_id: str, lang: str) -> None:
        """Ping IndexNow for a newly landed translation of an article."""
        self.ping(self.translation_change_urls(article_id, lang))

    def ping(self, urls: list[str] | Iterable[str]) -> None:
        """Notify IndexNow of added/updated/removed URLs. No-op without key/URLs."""
        key = (self._key() or "").strip()
        urls = dedupe_urls(urls)
        if not key or not urls:
            return
        host = self.site().split("://", 1)[-1].split("/", 1)[0]
        key_location = f"{self.site()}/{key}.txt"
        for start in range(0, len(urls), _MAX_URLS_PER_REQUEST):
            chunk = urls[start : start + _MAX_URLS_PER_REQUEST]
            _post_with_retry(
                {"host": host, "key": key, "keyLocation": key_location, "urlList": chunk},
                chunk,
            )


def _post_with_retry(payload: dict, urls_for_log: list[str]) -> None:
    """POST one IndexNow batch, retrying transient failures, swallowing the rest."""
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
