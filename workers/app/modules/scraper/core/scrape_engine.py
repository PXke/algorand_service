from __future__ import annotations

from urllib.parse import urlparse

from app.core import config


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _allowed_browser_domains() -> set[str]:
    raw = config.BROWSER_SCRAPE_DOMAINS.strip().lower()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def uses_browser_engine(scrape_url: str) -> bool:
    """
    Whether this source should use Playwright (hard targets / SPAs).
    """
    if config.SCRAPE_ENGINE_DEFAULT == "http":
        return scrape_url.startswith("browser://")
    if config.SCRAPE_ENGINE_DEFAULT == "browser":
        return scrape_url.startswith("http") or scrape_url.startswith("browser://")

    # auto
    if scrape_url.startswith("browser://"):
        return True

    if scrape_url.startswith("http"):
        host = _domain(scrape_url)
        return host in _allowed_browser_domains()

    return False
