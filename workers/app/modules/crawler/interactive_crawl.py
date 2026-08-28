"""Click-based crawling for domains href-following can't reach real content on.

A client-routed SPA (React/Vue app whose "pages" are just conditionally-
rendered UI state, not real navigable URLs) can render substantial-looking
text at any path a crawler tries -- the app's own router either shows the
home screen for an unrecognized route or an explicit not-found state -- but
that's not the same as finding NEW content. The actual content (an "About
this project" modal, a rankings panel, a demo/tutorial) only ever appears
after a click, with no href and no URL change at all (root-caused
2026-08-28, Lumi Rogue: `/about` and `/terms` genuinely 404 from the app's
own router; the real content sits behind footer buttons with no href).

This reuses PlaywrightSession's interactive_open/interactive_click (the
same mechanism the writer's play_interactive tool already gives an LLM
mid-compose, see research_tools.py), just run unattended by the crawl
pipeline against a domain flagged by
crawled_page_store.needs_interactive_crawl, rather than by a model deciding
what to click during one article's compose.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.scraper.core.browser_scrape import PlaywrightSession

logger = logging.getLogger(__name__)


def _synthetic_click_url(entry_url: str, click_text: str) -> str:
    """A stable, inspectable pseudo-URL for a click result with no real URL of its own -- deterministic per (entry_url, click_text) so a repeat interactive crawl updates the SAME stored page (via page_id_for_url's hash-of-url identity) instead of accumulating a new row every run."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", click_text.lower()).strip("-")[:60] or "click"
    return f"{entry_url.rstrip('/')}#interactive:{slug}"


def crawl_interactively(entry_url: str, *, service_id: str, max_steps: int | None = None) -> int:
    """Explore entry_url's visible UI by clicking through it (from the SAME entry state each time, not chaining clicks, so each result samples "what does button X do" rather than wandering an unpredictable path), storing each distinct resulting state as a crawled page. Returns the number of tool calls that reached index_crawled_page's "indexed"/"skipped" storage gate (not necessarily stored -- see its own soft-404/duplicate-content checks) minus outright click failures.

    Best-effort throughout: a missing Playwright install, a failed open, or
    any individual click failing all degrade to "explored less than hoped",
    never to a raised exception -- this runs unattended in the crawl
    pipeline, with nothing to report a traceback to.
    """
    from app.core.config import INTERACTIVE_CRAWL_MAX_STEPS
    from app.modules.scraper.core.browser_scrape import PlaywrightSession

    steps = max_steps if max_steps is not None else INTERACTIVE_CRAWL_MAX_STEPS
    try:
        session = PlaywrightSession()
    except ImportError:
        logger.warning("crawl_interactively: playwright not installed, skipping %s", entry_url)
        return 0

    attempted = 0
    try:
        try:
            session.interactive_open(entry_url)
            clickable = session.interactive_clickable_texts(limit=steps)
        except Exception:
            logger.warning("crawl_interactively: failed to open %s", entry_url, exc_info=True)
            return 0
        for click_text in clickable[:steps]:
            attempted += _click_and_store_one(session, entry_url, click_text, service_id=service_id)
    finally:
        session.close()
    return attempted


def _click_and_store_one(
    session: PlaywrightSession, entry_url: str, click_text: str, *, service_id: str
) -> int:
    """One explore-from-the-entry-state step: re-open entry_url fresh (so every click samples from the SAME baseline, not from wherever the previous click left the page), click click_text, and hand the result to index_crawled_page's own storage gates. Returns 1 if the click succeeded and reached the storage gate, 0 on any failure -- a single bad click_text (stale text, an element that vanished) must not abort the rest of the exploration."""
    from app.modules.scraper.core.browser_scrape import BrowserScrapeError
    from app.modules.search.tasks.index_tasks import index_crawled_page

    try:
        session.interactive_open(entry_url)
        result = session.interactive_click(click_text)
    except BrowserScrapeError:
        logger.debug("crawl_interactively: click %r failed on %s", click_text, entry_url)
        return 0
    except Exception:
        logger.warning(
            "crawl_interactively: unexpected error clicking %r on %s",
            click_text,
            entry_url,
            exc_info=True,
        )
        return 0
    try:
        index_crawled_page(
            url=_synthetic_click_url(entry_url, click_text),
            title=result.title or click_text,
            text=result.text,
            service_id=service_id,
        )
    except Exception:
        logger.warning(
            "crawl_interactively: failed to store click %r result", click_text, exc_info=True
        )
        return 0
    return 1
