from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup

from app.core.net_guard import guarded_get
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.web_fetch import html_to_plain_text

_USER_AGENT = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"


class HttpScraper(BaseScraper):
    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        # guarded_get rejects internal/private targets and re-checks each
        # redirect hop, so a planted link can't pivot the crawler to localhost.
        response = guarded_get(
            url,
            timeout=20.0,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        # Canonicalize on the POST-redirect URL so sites that redirect into one
        # another (e.g. algorand.co -> algorand.foundation) collapse to a single
        # source_url/domain downstream — otherwise each spawns a near-duplicate
        # article and evades the per-domain novelty/cooldown.
        final_url = str(response.url) or url
        soup = BeautifulSoup(response.text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title else ""
        from app.modules.scraper.core.page_metadata import (
            extract_og_image,
            extract_page_meta,
            extract_published_at,
        )

        og_image = extract_og_image(soup, final_url)
        text = html_to_plain_text(response.text)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        from app.modules.scraper.core.web_fetch import extract_content_links

        links = extract_content_links(response.text, final_url)

        return ScrapeResult(
            source_id=source_id,
            url=final_url,
            links=links,
            title=title,
            text=text,
            content_hash=content_hash,
            raw_html=response.text,
            og_image=og_image,
            published_at=extract_published_at(soup),
            meta=extract_page_meta(soup),
        )
