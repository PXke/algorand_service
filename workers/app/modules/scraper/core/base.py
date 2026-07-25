"""Base scrape-result shape and scraper interface."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScrapeResult:
    """A scraped page's extracted content and metadata."""
    source_id: str
    url: str
    title: str
    text: str
    content_hash: str
    # Raw response body (http lane only) — used to detect SPA shells.
    raw_html: str = ""
    # og:image / twitter:image from the page head — real article hero image.
    og_image: str = ""
    # ISO-8601 publish date from page metadata (article:published_time / JSON-LD
    # datePublished / <time>) — drives the recency gate. "" when absent.
    published_at: str = ""
    # Extra OG/meta we leverage: site_name, author, og_type, section, tags.
    meta: dict[str, str] = field(default_factory=dict)
    # In-content outbound links (absolute, deduped): [{"text", "url"}]. Given to
    # the composer as a research trail; NOT mixed into `text` (that feeds the
    # relevance classifier / novelty / content-hash, which links would pollute).
    links: list[dict[str, str]] = field(default_factory=list)


class BaseScraper:
    """Scraper interface implemented per source type."""
    def scrape(self, url: str, source_id: str) -> ScrapeResult:  # pragma: no cover - interface
        """Scrape one URL and return its extracted content and metadata."""
        raise NotImplementedError
