"""Extract and score outbound links from a fetched page."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _preview_has_algorand_signal(preview: dict[str, str]) -> bool:
    """True when a domain's landing-page preview actually mentions the Algorand ecosystem — the guard that keeps frontier auto-approve from crawling sites that merely scored on noise. Reject is never decided here; this only gates the no-human auto-approve, so a miss just means 'hold for review'."""
    blob = " ".join(
        (
            preview.get("preview_title", ""),
            preview.get("preview_description", ""),
            preview.get("preview_keywords", ""),
        )
    ).lower()
    return "algorand" in blob or "algo " in blob or " algo" in blob or "asa " in blob


_SKIP_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".pdf",
    ".zip",
    ".mp4",
    ".mp3",
    ".woff",
    ".woff2",
    ".xml",
    ".rss",
)


def extract_page_links(
    raw_html: str, base_url: str, *, limit: int = 60
) -> tuple[list[str], list[str]]:
    """Absolute http(s) links from a page's anchors: (same_domain, external)."""
    from urllib.parse import urljoin, urlparse

    from bs4 import BeautifulSoup

    if not raw_html:
        return [], []
    base_host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    same: list[tuple[str, str]] = []
    external: list[tuple[str, str]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(raw_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, anchor["href"]).split("#", 1)[0].strip()
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if href.lower().endswith(_SKIP_EXTENSIONS) or "/cdn-cgi/" in href:
            continue
        key = href.rstrip("/").lower()
        if key in seen or key == base_url.rstrip("/").lower():
            continue
        seen.add(key)
        text = " ".join(anchor.get_text(" ").split())[:200]
        host = parsed.hostname.lower().removeprefix("www.")
        (same if host == base_host else external).append((href, text))
        if len(seen) >= limit:
            break
    return same, external


def _expected_preview_failure(exc: BaseException) -> bool:
    """Routine fetch failures on the frontier — dead DNS, blocked hosts, timeouts."""
    import httpx

    from app.modules.scraper.core.scrape_cooldown import is_permanent_failure

    if is_permanent_failure(exc):
        return True
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError)):
        return True
    return bool(
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {401, 403, 404, 410, 429, 500, 502, 503, 504}
    )


def fetch_domain_preview(url: str) -> dict[str, str]:
    """Lightweight peek at a candidate domain: title + meta description + keywords only. One GET, no link-following, no storage, no Mistral — just enough for an admin to judge a pending domain without visiting it."""
    out = {"preview_title": "", "preview_description": "", "preview_keywords": ""}
    try:
        from bs4 import BeautifulSoup

        from app.core.net_guard import guarded_get

        resp = guarded_get(
            url,
            timeout=12.0,
            headers={"User-Agent": "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"},
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.title and soup.title.string:
            out["preview_title"] = soup.title.string.strip()[:200]
        for name, key in (("description", "preview_description"), ("keywords", "preview_keywords")):
            tag = soup.find("meta", attrs={"name": name}) or soup.find(
                "meta", attrs={"property": f"og:{name}"}
            )
            if tag and tag.get("content"):
                out[key] = tag["content"].strip()[:400]
        if not out["preview_description"]:
            og = soup.find("meta", attrs={"property": "og:description"})
            if og and og.get("content"):
                out["preview_description"] = og["content"].strip()[:400]
        # Predicted interest from the preview text alone (no full crawl).
        from app.modules.ai.publish_classifier import score_content_for_storage

        blob = " ".join((out["preview_title"], out["preview_description"], out["preview_keywords"]))
        out["preview_score"] = f"{score_content_for_storage(blob, url):.1f}"
    except Exception as exc:
        if _expected_preview_failure(exc):
            logger.info("domain preview unavailable for %s: %s", url, exc)
        else:
            logger.warning("failed to fetch domain preview for %s: %s", url, exc, exc_info=True)
    return out


def _enqueue_same_domain_links(
    same: list[tuple[str, str]], *, source: str, page_url: str, priority: int
) -> int:
    from app.modules.crawler.url_queue import enqueue_url

    enqueued = 0
    for url, _text in same:
        _, created = enqueue_url(
            url, source=source, priority=priority, metadata={"parent_url": page_url}
        )
        if created:
            enqueued += 1
    return enqueued


def _process_external_link(
    url: str,
    link_text: str,
    *,
    source: str,
    page_url: str,
    previews_done: int,
    preview_budget: int,
) -> tuple[list[str], bool]:
    """Apply the frontier policy to one external link. Returns (counter_keys_to_bump, preview_consumed).

    Only the HARD blocklist (generic platforms) + human rejects are skipped.
    We do NOT content-score auto-reject unknown domains: a landing page's
    preview text is a poor relevance signal and wrongly dead-ended real
    Algorand projects (pact.fi, perawallet, algorand.co, ...). Unknown domains
    are always HELD for review — a human (or auto-APPROVE later) decides; we
    never silently drop or reject them.
    """
    from app.core.config import FRONTIER_AUTO_APPROVE_ENABLED, FRONTIER_AUTO_APPROVE_SCORE
    from app.modules.crawler.domain_tracker import (
        domain_from_url,
        ensure_monitored_service,
        evaluate_frontier_link,
        is_protected_domain,
        record_domain_auto_approved,
        register_pending_domain,
    )
    from app.modules.crawler.url_queue import enqueue_url

    domain = domain_from_url(url)
    # One status read decides both the dead-end gate and the frontier state.
    state, dead_end = evaluate_frontier_link(domain)
    if dead_end:
        return ["dead_end_skipped"], False
    if state == "approved":
        _, created = enqueue_url(url, source=source, priority=20, metadata={"parent_url": page_url})
        return (["external"] if created else []), False
    if state == "pending":
        return ["held_pending"], False

    # Unknown domain. Previewing is a blocking GET + classifier pass, so cap how
    # many we do per page: past the budget, register the domain pending with no
    # preview (the classify_pending_domains task previews it later) instead of
    # stalling the drain worker on a link-heavy page.
    if previews_done >= preview_budget:
        register_pending_domain(domain, first_url=url, link_text=link_text, found_on=page_url)
        return ["held_no_preview"], False

    # Hold for review, UNLESS score-gated auto-approve is on and the preview
    # clears the bar (or the name carries an Algorand signal). Below the bar it
    # is still held pending — auto-approve never auto-rejects.
    preview = fetch_domain_preview(url)
    try:
        pscore = float(preview.get("preview_score", "0") or 0)
    except (TypeError, ValueError):
        pscore = 0.0
    # Auto-approve (= crawl now, no human) is reserved for domains that show a
    # real ecosystem signal: either a protected/Algorand-named domain, or a
    # preview that BOTH clears the score AND actually mentions Algorand. A bare
    # numeric score from the noisy keyword scorer is not enough — that is how
    # off-topic sites (e.g. recipe blogs reached via an ad/footer link) slipped
    # in. Below the bar the domain is still HELD pending, never auto-rejected.
    auto_approve = FRONTIER_AUTO_APPROVE_ENABLED and (
        is_protected_domain(domain)
        or (pscore >= FRONTIER_AUTO_APPROVE_SCORE and _preview_has_algorand_signal(preview))
    )
    register_pending_domain(
        domain,
        first_url=url,
        link_text=link_text,
        found_on=page_url,
        preview=preview,
        approved=auto_approve,
    )
    if not auto_approve:
        return ["held_pending"], True

    keys = ["auto_approved"]
    _, created = enqueue_url(url, source=source, priority=20, metadata={"parent_url": page_url})
    if created:
        keys.append("external")
    # Auto-approve = full approval: also register the monitored source (as the
    # admin approve does), so the domain's evolution can reach the publish
    # queue — not just the research corpus.
    ensure_monitored_service(domain, scrape_url=url)
    record_domain_auto_approved(domain)
    return keys, True


def enqueue_page_links(
    *,
    raw_html: str,
    page_url: str,
    source: str,
) -> dict[str, int]:
    """One-hop crawl frontier: queue links found on a crawled page.

    Same-domain links (e.g. articles behind a blog index) are queued directly;
    external links pass the dead-end gate (platform blocklist + domains the
    admin marked irrelevant); everything else unknown is held for review.
    """
    from app.core.config import WEB_LINK_DISCOVERY_ENABLED

    counts = {
        "same_domain": 0,
        "external": 0,
        "dead_end_skipped": 0,
        "held_pending": 0,
        "held_no_preview": 0,
        "auto_approved": 0,
    }
    if not WEB_LINK_DISCOVERY_ENABLED or not raw_html:
        return counts

    from app.core.config import (
        CRAWL_INITIAL_HARVEST_PRIORITY,
        CRAWL_INITIAL_HARVEST_TARGET,
        CRAWL_MAX_PAGES_PER_DOMAIN,
        FRONTIER_PREVIEW_MAX_PER_PAGE,
    )
    from app.modules.crawler.domain_tracker import domain_crawl_count, domain_from_url

    same, external = extract_page_links(raw_html, page_url)

    # Per-domain page budget (counts FETCHED pages this rolling window): front-
    # load the first N at high priority, the rest at normal, then HARD STOP so a
    # huge site (e.g. allo.info's per-transaction pages) can't explode the queue.
    page_domain = domain_from_url(page_url)
    crawl_count = domain_crawl_count(page_domain)
    if crawl_count >= CRAWL_MAX_PAGES_PER_DOMAIN:
        counts["budget_exhausted"] = 1
    else:
        same_domain_priority = (
            CRAWL_INITIAL_HARVEST_PRIORITY if crawl_count < CRAWL_INITIAL_HARVEST_TARGET else 25
        )
        counts["same_domain"] = _enqueue_same_domain_links(
            same, source=source, page_url=page_url, priority=same_domain_priority
        )

    previews_done = 0
    for url, link_text in external:
        keys, used_preview = _process_external_link(
            url,
            link_text,
            source=source,
            page_url=page_url,
            previews_done=previews_done,
            preview_budget=FRONTIER_PREVIEW_MAX_PER_PAGE,
        )
        for key in keys:
            counts[key] += 1
        if used_preview:
            previews_done += 1
    return counts
