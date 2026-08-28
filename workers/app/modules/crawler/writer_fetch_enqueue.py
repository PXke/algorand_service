"""Enqueue URLs the writer researches via fetch_url into the crawl frontier.

The writer's fetch_url is a lightweight peek (HTTP, chunked, no harvest). When
the model explicitly asks to read a page, queue it for a full web-crawl pass so
the page lands in crawled_pages + Typesense for future search_crawled_pages calls.
Best-effort only — never raises into the compose/tool path.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fetch_result_worth_crawling(
    result: dict[str, Any], *, is_continuation: bool
) -> tuple[str, str] | None:
    """The (url, text) to enqueue, or None if this fetch isn't worth a full crawl pass.

    Root-caused 2026-08-28 (Lumi Rogue): a writer hunting for a route that
    turned out not to exist tried ~20 URL guesses via fetch_url; a client-side
    router's own "not found" fallback page is real text (passes the length
    check below) but is never worth a full crawl pass -- queuing it anyway is
    how one research session's exploratory guessing durably pollutes the
    crawl corpus.
    """
    from app.modules.crawler.crawled_page_store import looks_like_soft_404

    if is_continuation:
        return None  # scroll windows — one queue entry per URL
    if not isinstance(result, dict) or result.get("error"):
        return None
    url = (result.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    text = result.get("text") or ""
    # Skip near-empty shells — the crawl pipeline can't harvest them either.
    if int(result.get("chunk_chars") or len(text)) < 80:
        return None
    if looks_like_soft_404(text):
        return None
    return url, text


def maybe_enqueue_writer_fetched_url(
    result: dict[str, Any],
    *,
    is_continuation: bool = False,
    compose_source: str = "",
    service_id: str = "",
) -> bool:
    """Queue a successful first-chunk fetch_url result for harvesting."""
    from app.core.config import (
        URL_QUEUE_ENABLED,
        WRITER_FETCH_ENQUEUE_ENABLED,
        WRITER_FETCH_ENQUEUE_PRIORITY,
    )

    if not URL_QUEUE_ENABLED or not WRITER_FETCH_ENQUEUE_ENABLED:
        return False
    worth_crawling = _fetch_result_worth_crawling(result, is_continuation=is_continuation)
    if worth_crawling is None:
        return False
    url, _text = worth_crawling
    try:
        from app.modules.crawler.url_queue import enqueue_url

        meta: dict[str, str] = {"via": "writer_fetch_url"}
        if compose_source:
            meta["compose_source"] = compose_source[:512]
        if service_id:
            meta["service_id"] = service_id[:256]
        _, created = enqueue_url(
            url,
            source="writer_fetch",
            priority=WRITER_FETCH_ENQUEUE_PRIORITY,
            metadata=meta,
        )
        if created:
            logger.info("queued writer fetch for crawl: %s", url[:200])
        return created
    except Exception:
        logger.debug("failed to enqueue writer fetch url %s", url[:120], exc_info=True)
        return False
