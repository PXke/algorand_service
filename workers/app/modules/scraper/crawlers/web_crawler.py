"""CrawlerDriver implementation for regular web sources, with SPA fallback."""

from __future__ import annotations

import logging

import httpx

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.browser_scraper import BrowserScraper
from app.modules.scraper.core.http_scraper import HttpScraper
from app.modules.scraper.core.scrape_engine import uses_browser_engine
from app.modules.scraper.crawler_registry import is_web_spa_enabled
from app.modules.scraper.crawler_types import CrawlerType

logger = logging.getLogger(__name__)

# SPA root containers in the RAW html (extracted text never contains tags).
_SPA_ROOT_MARKERS = ('id="root"', "id='root'", 'id="app"', "id='app'", "__next", "__nuxt", "ng-app")
_MIN_HTTP_TEXT = 300
# HTTP statuses where a browser engine might still succeed: anti-bot gates and
# rate limits often serve a JS challenge or block the plain client by UA. A hard
# error like 404/410 means the page is genuinely absent, so don't waste a browser
# load (and don't mask the real status behind "insufficient visible text").
_BROWSER_RETRY_STATUSES = frozenset({403, 429, 503})
# With an SPA root present, this much extracted text still means most of the
# page is client-rendered — fall back to the browser engine.
_SPA_TEXT_SUFFICIENT = 5000


def _browser_might_help(exc: Exception) -> bool:
    """Whether retrying a failed HTTP fetch in a browser could plausibly succeed.

    Network/timeout errors and gate-like statuses (403/429/503) are worth a
    browser retry; a hard HTTP status (404/410/etc) is not — the page is absent,
    so we re-raise the original error rather than masking it behind a browser
    "insufficient visible text" failure.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _BROWSER_RETRY_STATUSES
    return True


def needs_spa_fallback(text: str, raw_html: str = "") -> bool:
    """Whether HTTP-fetched text is too thin (or an SPA shell) to trust, and a Playwright render should be tried instead. Shared by the crawler pipeline and any other caller (e.g. the writer's fetch_url tool) that fetches arbitrary pages over plain HTTP first."""
    from app.core.config import SPA_FALLBACK_ENABLED

    if not SPA_FALLBACK_ENABLED:
        return False
    if len(text) < _MIN_HTTP_TEXT:
        return True
    raw = raw_html.lower()
    has_spa_root = any(marker in raw for marker in _SPA_ROOT_MARKERS)
    return has_spa_root and len(text) < _SPA_TEXT_SUFFICIENT


class WebCrawlerDriver:
    """Generic web: HTTP first, Playwright fallback for thin or SPA pages."""

    crawler_type = CrawlerType.WEB.value

    def get_scraper(self, scrape_url: str) -> BaseScraper:
        """Return the HTTP or browser scraper to use for a given URL."""
        if uses_browser_engine(scrape_url):
            if not is_web_spa_enabled():
                msg = "web SPA sub-lane disabled (CRAWLER_WEB_SPA_ENABLED=0)"
                raise WebSpaDisabledError(msg)
            return BrowserScraper()
        # Plain HTTP with transparent Playwright retry for thin/SPA pages, so
        # every lane (publish pipeline included) benefits from the fallback.
        return _SmartWebScraper(self)

    def scrape_with_fallback(self, scrape_url: str, source_id: str) -> ScrapeResult:
        """Scrape via HTTP, falling back to the browser scraper for thin or SPA pages."""
        http = HttpScraper()
        try:
            result = http.scrape(scrape_url, source_id)
        except Exception as exc:
            if is_web_spa_enabled() and _browser_might_help(exc):
                return BrowserScraper().scrape(scrape_url, source_id)
            raise

        if needs_spa_fallback(result.text, raw_html=result.raw_html) and is_web_spa_enabled():
            return BrowserScraper().scrape(scrape_url, source_id)
        return result

    def scrape(self, scrape_url: str, source_id: str) -> ScrapeResult:
        """Scrape one URL, falling back to the browser scraper for thin or SPA pages."""
        return self.scrape_with_fallback(scrape_url, source_id)

    def scrape_from_queue_item(self, item: dict) -> dict[str, object]:
        """Full discovery pipeline for a dequeued URL queue item."""
        from app.modules.ai.publish_classifier import (
            is_content_quality_sufficient,
            service_id_for_url,
        )
        from app.modules.crawler.discovery_store import store_discovery_content
        from app.modules.crawler.domain_tracker import (
            domain_crawl_budget_exhausted,
            domain_from_url,
            is_admin_approved_domain,
            record_domain_crawl,
            should_recrawl_domain,
        )
        from app.modules.crawler.url_queue import (
            mark_url_crawled,
            mark_url_done,
            recently_crawled,
        )

        url = str(item.get("url", ""))
        queue_id = str(item.get("queue_id", ""))
        source = str(item.get("source", "web"))

        # Per-URL recrawl cooldown: don't fetch the same link twice within the
        # window even if it slipped back into the queue.
        if recently_crawled(url):
            if queue_id:
                mark_url_done(queue_id, status="skipped")
            return {"status": "skipped", "reason": "url_recrawl_cooldown", "url": url}

        domain = domain_from_url(url)
        if not should_recrawl_domain(domain):
            if queue_id:
                mark_url_done(queue_id, status="skipped")
            return {"status": "skipped", "reason": "domain_recrawl_cooldown", "url": url}

        # An admin explicitly vouching for a domain outranks the content-quality
        # floor below — that gate exists to filter anonymous auto-discovery,
        # not to second-guess a human relevance call. It does NOT outrank the
        # page budget just below: that's a volume/politeness cap, not a
        # relevance judgment, and bypassing it too let one-hop link-following
        # spider an entire large site with no limit once its admin-approved
        # flag (however it got set) stopped anything from ever stopping it
        # (root-caused 2026-07-21: python.org/nytimes.com/climatetrade.com
        # crawled dozens of subpages each, hit 429s on wfp.medium.com).
        admin_approved = is_admin_approved_domain(domain)

        # Per-domain page budget: drop already-queued pages for a domain that has
        # hit its cap this window (e.g. a huge site queued thousands of links
        # before the cap kicked in) — never fetch beyond the budget, admin-
        # approved or not.
        if domain_crawl_budget_exhausted(domain):
            if queue_id:
                mark_url_done(queue_id, status="skipped")
            return {"status": "skipped", "reason": "domain_page_budget_exhausted", "url": url}

        # robots.txt politeness: skip URLs the host disallows for our crawler.
        from app.modules.crawler.robots import is_allowed

        if not is_allowed(url):
            if queue_id:
                mark_url_done(queue_id, status="robots_disallowed")
            return {"status": "skipped", "reason": "robots_disallowed", "url": url}

        service_id = service_id_for_url(url)
        # Stamp the cooldown now (before the fetch) so any outcome — success or
        # failure — keeps this exact link out of the crawler for the window.
        mark_url_crawled(url)
        try:
            result = self.scrape_with_fallback(url, service_id)
        except Exception as exc:
            if queue_id:
                mark_url_done(queue_id, status="failed")
            return {"status": "error", "url": url, "detail": str(exc)}

        if not admin_approved and not is_content_quality_sufficient(result.text):
            # A thin / off-topic page is a per-PAGE signal only. Drop the page, but
            # do NOT demote the domain or zero its score on one bad page — the
            # per-URL cooldown (mark_url_crawled above) already prevents refetching
            # this exact link, and the domain's relevance verdict stays untouched.
            if queue_id:
                mark_url_done(queue_id, status="low_quality")
            return {"status": "skipped", "reason": "low_content_quality", "url": url}

        outcome = store_discovery_content(
            url=url,
            page_title=result.title,
            page_text=result.text,
            source=source,
        )
        # Count this fetched page against the domain's rolling page budget.
        record_domain_crawl(domain)
        # Harvest: persist the crawled page (crawled_pages_by_domain + Typesense)
        # so it becomes searchable content and counts toward the domain's page
        # total (admin "pages crawled" view + the initial-harvest priority gate).
        # index_crawled_page applies its own in-scope relevance classifier.
        try:
            from app.modules.search.tasks.index_tasks import index_crawled_page

            index_crawled_page.delay(
                url=url,
                title=result.title,
                text=result.text,
                service_id=service_id,
            )
        except Exception:
            logger.warning("failed to enqueue crawled-page indexing for %s", url, exc_info=True)
        # One-hop frontier: this page passed the quality gate, so its links are
        # worth queueing (dead-end domains are filtered inside) — unless the
        # admin explicitly approved just this one page (single_page_only),
        # which must never spider the rest of the site.
        no_follow = item.get("metadata", {}).get("no_follow_links") == "true"
        if not no_follow:
            try:
                from app.modules.scraper.core.link_extractor import enqueue_page_links

                enqueue_page_links(
                    raw_html=result.raw_html,
                    page_url=url,
                    source="web",
                )
            except Exception:
                logger.warning("failed to enqueue page links from %s", url, exc_info=True)
        if queue_id:
            mark_url_done(queue_id)
        return {
            "status": outcome.status,
            "url": url,
            "storage_score": outcome.storage_score,
            "category": outcome.category,
            "article_id": outcome.article_id,
            "review_id": outcome.review_id,
            "reason": outcome.reason,
        }


class WebSpaDisabledError(Exception):
    """Raised when a page needs the SPA (browser) fallback but it's disabled."""
    pass


class _SmartWebScraper(BaseScraper):
    """HTTP first, transparent Playwright retry for thin or SPA pages."""

    def __init__(self, driver: WebCrawlerDriver) -> None:
        self._driver = driver

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        return self._driver.scrape_with_fallback(url, source_id)
