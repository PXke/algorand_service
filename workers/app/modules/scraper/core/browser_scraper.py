"""BaseScraper implementation backed by the browser fetch path."""

from __future__ import annotations

import hashlib

from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.browser_scrape import (
    BrowserScrapeError,
    PlaywrightSession,
    fetch_page,
    resolve_browser_target_url,
)


class BrowserScraper(BaseScraper):
    """Playwright-based scraper for hard targets (SPAs, heavy JS sites).

    Registry: browser://https://… or https://… on an allowlisted domain.
    """

    def scrape(
        self,
        url: str,
        source_id: str,
        *,
        playwright_session: PlaywrightSession | None = None,
    ) -> ScrapeResult:
        """Scrape one URL via Playwright and return its extracted content and metadata.

        playwright_session: reuse a caller-owned session instead of paying
        for a fresh Chromium launch for this one URL -- see fetch_page's
        docstring. Optional and caller-owned: this never closes it.
        """
        target = self._resolve_url(url)
        if not target:
            msg = f"cannot resolve browser scrape url: {url!r}"
            raise BrowserScrapeError(msg)

        try:
            page = fetch_page(target, playwright_session=playwright_session)
        except BrowserScrapeError:
            raise
        except Exception as exc:
            msg = f"playwright scrape failed for {target}: {exc}"
            raise BrowserScrapeError(msg) from exc

        # Canonicalize on the post-redirect URL (see http_scraper) so redirecting
        # domains collapse to one source_url for novelty/cooldown.
        final_url = getattr(page, "final_url", "") or url
        title = page.title or _default_title(url)
        text = page.text
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Extract the hero image from the RENDERED html too (SPA pages otherwise
        # produced imageless articles → empty front-page tiles).
        og_image = ""
        try:
            from bs4 import BeautifulSoup

            from app.modules.scraper.core.page_metadata import extract_og_image

            og_image = extract_og_image(BeautifulSoup(page.html or "", "html.parser"), final_url)
        except Exception:
            og_image = ""
        links = []
        try:
            from app.modules.scraper.core.web_fetch import extract_content_links

            links = extract_content_links(page.html or "", final_url)
        except Exception:
            links = []
        return ScrapeResult(
            source_id=source_id,
            url=final_url,
            title=title,
            text=text,
            content_hash=content_hash,
            raw_html=page.html,
            og_image=og_image,
            links=links,
        )

    def _resolve_url(self, scrape_url: str) -> str | None:
        return resolve_browser_target_url(scrape_url)


def _default_title(_scrape_url: str) -> str:
    return "Web page"
