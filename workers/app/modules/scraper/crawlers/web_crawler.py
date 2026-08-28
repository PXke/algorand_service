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
# The framework-named ones (__next/__nuxt/ng-app, id="root"/"app") only catch
# a page built with that exact framework's default scaffold -- a hand-rolled
# or vanilla-JS SPA (root-caused 2026-08-10: pixelcity.aetheralabs.es uses
# <div class="app-shell">, matching none of these) needs its own root wrapper
# to be recognized too. This list is evidence-based, not exhaustive -- extend
# it as new live specimens turn up rather than trying to enumerate every
# possible convention up front.
_SPA_ROOT_MARKERS = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    'id="app-root"',
    "id='app-root'",
    'class="app-shell"',
    "class='app-shell'",
    'class="app-root"',
    "class='app-root'",
    "__next",
    "__nuxt",
    "ng-app",
)
_MIN_HTTP_TEXT = 300
# HTTP statuses where a browser engine might still succeed: anti-bot gates and
# rate limits often serve a JS challenge or block the plain client by UA. A hard
# error like 404/410 means the page is genuinely absent, so don't waste a browser
# load (and don't mask the real status behind "insufficient visible text").
_BROWSER_RETRY_STATUSES = frozenset({403, 429, 503})
# With an SPA root present, this much extracted text still means most of the
# page is client-rendered — fall back to the browser engine.
_SPA_TEXT_SUFFICIENT = 5000
# Collapsed accordion/FAQ markup in the raw pre-render HTML — present in the
# server-rendered HTML even before any click, since JS only toggles it.
# Signals content a plain-HTTP fetch cannot see regardless of overall text
# length (a page full of question TITLES with no answers isn't "thin").
_COLLAPSED_ACCORDION_MARKERS = ('aria-expanded="false"', "aria-expanded='false'", "<details")
# A visible "still loading"/"empty" placeholder in the EXTRACTED TEXT itself
# (not raw markup) — the framework-agnostic backstop for exactly the shape of
# bug the root-marker list above can only ever partially catch: root-caused
# live 2026-08-10, pixelcity.aetheralabs.es's gallery page's raw pre-JS HTML
# read comfortably over _MIN_HTTP_TEXT (a genuine "SIN OBRAS MINTEADAS AUN" /
# "no works minted yet" placeholder, ~680 chars) AND had no recognized SPA
# root marker, so it passed both checks above as if it were real, settled
# content. This targets the SYMPTOM (a visible loading/empty placeholder)
# rather than any one framework's markup convention, so it generalizes past
# whatever root-container naming a given site happens to use. Evidence-based
# and short by design, same rationale as _SPA_ROOT_MARKERS above — the
# Spanish forms are here because two unrelated Spanish-speaker-run projects
# hit this exact bug shape in one week (LumiRogue, Pixel City).
_LOADING_PLACEHOLDER_MARKERS = (
    "cargando...",
    "consultando...",
    "loading...",
    "please enable javascript",
)


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
    """Whether HTTP-fetched text is too thin (or an SPA shell, has collapsed accordion/FAQ content, or visibly shows a loading/empty placeholder) to trust, and a Playwright render should be tried instead. Shared by the crawler pipeline and any other caller (e.g. the writer's fetch_url tool) that fetches arbitrary pages over plain HTTP first. The Playwright render alone isn't enough for the accordion case — see browser_scrape.py's _expand_collapsed_content, which clicks/opens the collapsed elements before extracting text."""
    from app.core.config import SPA_FALLBACK_ENABLED

    if not SPA_FALLBACK_ENABLED:
        return False
    if len(text) < _MIN_HTTP_TEXT:
        return True
    raw = raw_html.lower()
    has_spa_root = any(marker in raw for marker in _SPA_ROOT_MARKERS)
    if has_spa_root and len(text) < _SPA_TEXT_SUFFICIENT:
        return True
    if any(marker in raw for marker in _COLLAPSED_ACCORDION_MARKERS):
        return True
    low_text = text.lower()
    return any(marker in low_text for marker in _LOADING_PLACEHOLDER_MARKERS)


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

    @staticmethod
    def _skip(queue_id: str, url: str, *, status: str, reason: str) -> dict[str, object]:
        from app.modules.crawler.url_queue import mark_url_done

        if queue_id:
            mark_url_done(queue_id, status=status)
        return {"status": "skipped", "reason": reason, "url": url}

    def _pre_fetch_gate(
        self, url: str, domain: str, *, queue_id: str, source: str = "web"
    ) -> dict[str, object] | None:
        """Sequential pre-fetch checks (recrawl cooldowns, page budget, robots.txt). Returns a skip result if any blocks the fetch, else None to proceed."""
        from app.modules.crawler.domain_tracker import (
            domain_crawl_budget_exhausted,
            should_recrawl_domain,
        )
        from app.modules.crawler.robots import is_allowed
        from app.modules.crawler.url_queue import recently_crawled

        # Per-URL recrawl cooldown: don't fetch the same link twice within the
        # window even if it slipped back into the queue. EXCEPT the one-shot
        # seed an admin's domain approval creates (source="frontier_approval",
        # see admin/api/routes.py's _seed_domain_crawl) — that is a rare,
        # deliberate "crawl this now" action, not routine re-discovery, and
        # the 30-day cooldown (CRAWL_URL_RECRAWL_COOLDOWN_SECONDS) can easily
        # already be running from a completely unrelated incidental fetch
        # (e.g. a writer's fetch_url link-following into this URL from some
        # other article's research). Root-caused 2026-08-06: an admin-approved
        # domain's discovery seed was silently skipped this way, with no
        # visible error anywhere, the day after an unrelated writer session
        # happened to fetch the same URL. Domain-level cooldown/budget/robots
        # below are NOT bypassed — those are politeness/volume caps, not a
        # relevance judgment, same reasoning as the content-quality gate a few
        # lines below in scrape_from_queue_item.
        if source != "frontier_approval" and recently_crawled(url):
            return self._skip(queue_id, url, status="skipped", reason="url_recrawl_cooldown")
        if not should_recrawl_domain(domain):
            return self._skip(queue_id, url, status="skipped", reason="domain_recrawl_cooldown")
        # Per-domain page budget: drop already-queued pages for a domain that has
        # hit its cap this window (e.g. a huge site queued thousands of links
        # before the cap kicked in) — never fetch beyond the budget, admin-
        # approved or not.
        if domain_crawl_budget_exhausted(domain):
            return self._skip(
                queue_id, url, status="skipped", reason="domain_page_budget_exhausted"
            )
        # robots.txt politeness: skip URLs the host disallows for our crawler.
        if not is_allowed(url):
            return self._skip(queue_id, url, status="robots_disallowed", reason="robots_disallowed")
        return None

    @staticmethod
    def _enqueue_discovered_links(item: dict, result: ScrapeResult, url: str) -> None:
        """One-hop frontier: queue this page's links (dead-end domains are filtered inside) — unless the admin explicitly approved just this one page (single_page_only), which must never spider the rest of the site."""
        no_follow = item.get("metadata", {}).get("no_follow_links") == "true"
        if no_follow:
            return
        try:
            from app.modules.scraper.core.link_extractor import enqueue_page_links

            enqueue_page_links(raw_html=result.raw_html, page_url=url, source="web")
        except Exception:
            logger.warning("failed to enqueue page links from %s", url, exc_info=True)

    def scrape_from_queue_item(self, item: dict) -> dict[str, object]:
        """Full discovery pipeline for a dequeued URL queue item."""
        from app.core.config import SINGLE_PAGE_AUTOCOMPOSE_ENABLED
        from app.modules.ai.publish_classifier import (
            is_content_quality_sufficient,
            service_id_for_url,
        )
        from app.modules.crawler.discovery_store import store_discovery_content
        from app.modules.crawler.domain_tracker import (
            domain_from_url,
            is_admin_approved_domain,
            record_domain_crawl,
            single_page_service_id,
        )
        from app.modules.crawler.url_queue import mark_url_crawled, mark_url_done

        url = str(item.get("url", ""))
        queue_id = str(item.get("queue_id", ""))
        source = str(item.get("source", "web"))
        domain = domain_from_url(url)

        gate_skip = self._pre_fetch_gate(url, domain, queue_id=queue_id, source=source)
        if gate_skip is not None:
            return gate_skip

        # An admin explicitly vouching for a domain outranks the content-quality
        # floor below — that gate exists to filter anonymous auto-discovery,
        # not to second-guess a human relevance call. It does NOT outrank the
        # page budget in _pre_fetch_gate: that's a volume/politeness cap, not a
        # relevance judgment, and bypassing it too let one-hop link-following
        # spider an entire large site with no limit once its admin-approved
        # flag (however it got set) stopped anything from ever stopping it
        # (root-caused 2026-07-21: python.org/nytimes.com/climatetrade.com
        # crawled dozens of subpages each, hit 429s on wfp.medium.com).
        admin_approved = is_admin_approved_domain(domain)

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

        # Count this fetched page against the domain's rolling page budget
        # regardless of the quality-gate verdict below — a low-quality page
        # still cost a fetch, and a domain that keeps returning thin/off-topic
        # pages must not get an effectively unlimited crawl budget just
        # because none of those pages individually clear the content-quality
        # floor.
        record_domain_crawl(domain)

        if not admin_approved and not is_content_quality_sufficient(result.text):
            # A thin / off-topic page is a per-PAGE signal only. Drop the page, but
            # do NOT demote the domain or zero its score on one bad page — the
            # per-URL cooldown (mark_url_crawled above) already prevents refetching
            # this exact link, and the domain's relevance verdict stays untouched.
            return self._skip(queue_id, url, status="low_quality", reason="low_content_quality")

        outcome = store_discovery_content(
            url=url,
            page_title=result.title,
            page_text=result.text,
            source=source,
        )
        # Single-page mode (admin approved just this ONE url — no_follow_links
        # is stamped only by that path, see _enqueue_discovered_links below):
        # one-shot compose via the exact same shared path every other lane
        # (full-site service watch, mail, Bluesky, ...) already uses, so it
        # gets novelty/dedupe/priority/the Mistral credit-guard/publish caps
        # for free. Reuses the page just fetched above — no second HTTP call.
        no_follow = item.get("metadata", {}).get("no_follow_links") == "true"
        if no_follow and admin_approved and SINGLE_PAGE_AUTOCOMPOSE_ENABLED:
            try:
                from app.modules.newspaper.ingest_signal import ingest_publish_signal

                ingest_publish_signal(
                    service_id=single_page_service_id(url),
                    display_name=domain,
                    source_url=url,
                    page_title=result.title,
                    page_text=result.text,
                    source_kind="web",
                    match_kind="single_page",
                    match_value=domain,
                    txid="",
                    round_num=0,
                    is_first_override=True,
                )
            except Exception:
                logger.warning("single-page compose failed for %s", url, exc_info=True)
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
                outbound_links=tuple(link.get("url", "") for link in result.links),
                published_at=result.published_at,
            )
        except Exception:
            logger.warning("failed to enqueue crawled-page indexing for %s", url, exc_info=True)
        self._enqueue_discovered_links(item, result, url)
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
