"""Celery tasks that drain the URL frontier queue and classify domains."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.celery_app import celery_app
from app.core.config import URL_QUEUE_ENABLED
from app.core.redis_lock import single_flight
from app.modules.crawler.url_queue import (
    dequeue_url,
    pending_url_count,
    reclaim_stale_processing_urls,
)
from app.modules.scraper.core.browser_scrape import maybe_start_session
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession

    from app.modules.search.classifier.score import ClassifierResult


@celery_app.task(name="app.tasks.crawler.drain_url_queue")
# Beat fires every URL_QUEUE_DRAIN_SECONDS (as low as 10s); a batch of
# max_items real fetches (with Playwright fallback) can easily outrun that
# interval, so without single_flight a slow tick overlaps the next one and
# both drain the same pending pool concurrently (2x the work, external sites
# hit 2x). Lock TTL pinned to the celery-wide hard task_time_limit (not the
# drain interval) per CLAUDE.md invariant 5 -- "lock TTL >= the task's soft
# time limit" -- so the lock always outlives the run even if the soft-limit
# interrupt doesn't land in time and celery has to hard-kill the worker; the
# beat entry itself separately sets `expires=URL_QUEUE_DRAIN_SECONDS` so a
# tick that never got a free worker slot in time is dropped rather than run
# stale (see celery_app.py's drain-url-queue beat entry).
@single_flight(lambda *_a, **_kw: "crawler:drain_url_queue", ttl=celery_app.conf.task_time_limit)
def drain_url_queue(*, max_items: int = 5) -> dict[str, object]:
    """Dequeue URLs and run the web discovery pipeline.

    One PlaywrightSession is started up front and shared across this
    batch's items, instead of scrape_from_queue_item's own browser-fallback
    path launching a fresh throwaway Chromium per item -- root-caused
    2026-08-28: with up to max_items (as high as 10 per tick) each
    potentially hitting the SPA fallback, this beat could pay for up to
    max_items separate Chromium launches (~2-5s + ~300MB RSS each) every
    URL_QUEUE_DRAIN_SECONDS (as low as 10s), a real contributor to the
    scrape-pool saturation incident. maybe_start_session() never raises
    (returns None if the SPA lane is disabled or launch fails) and the
    session is always closed in `finally`, even if an item's scrape raises.
    """
    if not URL_QUEUE_ENABLED:
        return {"status": "skipped", "reason": "url_queue_disabled", "processed": 0}

    driver = WebCrawlerDriver()
    results: list[dict[str, object]] = []
    processed = 0
    session = maybe_start_session()
    try:
        for _ in range(max_items):
            item = dequeue_url()
            if item is None:
                break
            outcome = driver.scrape_from_queue_item(item, playwright_session=session)
            results.append(outcome)
            processed += 1
    finally:
        if session is not None:
            session.close()

    return {
        "status": "ok",
        "processed": processed,
        "remaining": pending_url_count(),
        "results": results,
    }


# extract_page_links' limit caps same-domain + external links COMBINED, in DOM
# order. A real marketing site's nav/footer/social/legal boilerplate alone can
# fill 30 slots before a substantive link ever shows up: quantoz.com's two
# allo.info explorer links sit at index 53-54 of 59 total hrefs, well past 30
# (2026-07-21). Parsing more anchors from HTML already in memory is nearly
# free — no extra network I/O — so there's no real cost to scanning generously
# even though only a handful of same-domain links actually get crawled further.
_LINK_SCAN_LIMIT = 200


def _cached_page_body(url: str, *, max_age_seconds: int | None = None) -> str | None:
    """Reuse an already-harvested page's body instead of fetching it over HTTP again. crawled_pages_by_id's page_id is a deterministic hash of the url (crawled_page_store.page_id_for_url), so this is a single point lookup, never a domain scan. Root-caused 2026-07-22: classify's shallow sampler and drain_url_queue's routine crawl independently re-fetch the same pages whenever a domain's site is heavily interlinked ("tight cluster" sites that share the same handful of pages across many links). Best-effort — a cache miss or any Cassandra hiccup just means "go fetch it live"."""
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session
    from app.core.config import CRAWL_URL_RECRAWL_COOLDOWN_SECONDS
    from app.core.statements import CrawledPageStmts
    from app.modules.crawler.crawled_page_store import page_id_for_url

    max_age = CRAWL_URL_RECRAWL_COOLDOWN_SECONDS if max_age_seconds is None else max_age_seconds
    try:
        session = get_cassandra_session()
        row = session.execute(CrawledPageStmts.GET_BODY, (page_id_for_url(url),)).one()
    except Exception:
        return None
    if not row or not row.body:
        return None
    crawled_at = row.crawled_at
    if crawled_at is not None:
        if crawled_at.tzinfo is None:
            crawled_at = crawled_at.replace(tzinfo=UTC)
        if (datetime.now(tz=UTC) - crawled_at).total_seconds() > max_age:
            return None
    return row.body


def _cached_domain_urls(domain: str, limit: int = 20) -> list[str]:
    """Urls of pages already harvested for this domain (single-partition read), newest first — used as a fallback page-sample pool when the landing page itself was a cache hit and so didn't yield fresh same-domain links to follow. Best-effort — a Cassandra hiccup just means no extras."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import CrawledPageStmts

    try:
        session = get_cassandra_session()
        rows = session.execute(CrawledPageStmts.LIST_BY_DOMAIN, (domain, limit))
    except Exception:
        return []
    return [row.url for row in rows if getattr(row, "url", None)]


def _sample_domain_pages(
    driver: WebCrawlerDriver, landing_url: str, domain: str, max_pages: int
) -> tuple[list[tuple[str, str, tuple[str, ...]]], int]:
    """(pages, same_domain_link_count). pages: (url, text, outbound_external_links) for the landing page plus up to max_pages-1 same-domain links found on it. Best-effort — a same-domain page that fails to fetch is just skipped, never counted as an error against the domain. The external links travel with each page so the caller can feed them to score_page's explorer-link signal — a multi-chain service's product page can link straight to its Algorand explorer entry without ever using the word "algorand" in its own text (quantoz.com/EURQ, 2026-07-21).

    same_domain_link_count is the landing page's TRUE same-domain link fan-out
    (before truncating to max_pages-1) — a free by-product of the link
    extraction already happening here, used as a full-site-vs-single-page
    density signal (see suggest_full_site in domain_tracker.py). 0 when the
    landing page was a cache hit with no raw_html to parse (rare — see below).

    Each fetch checks the crawled-page cache first (_cached_page_body) so a
    page drain_url_queue or an earlier sample already harvested isn't
    re-fetched over HTTP — a cache hit has no raw_html, so it contributes no
    links; if that happens on the landing page itself, _cached_domain_urls
    supplies fallback candidates from this domain's existing harvest instead
    of shrinking the sample to one page (2026-07-22).

    A cache hit skips the gate entirely (no new request is made, so there is
    nothing to be polite about or count against the budget) — only a LIVE
    fetch checks domain_crawl_budget_exhausted / domain_in_cooldown (once,
    for the whole sample — this function fetches at most FRONTIER_CLASSIFY_
    SAMPLE_PAGES pages, not deep_classify_domain's up-to-200, so a single
    upfront check is enough) and is_allowed (robots.txt, per URL). Previously
    this called WebCrawlerDriver.scrape_with_fallback directly with none of
    those three checks at all, unlike every routine drain_url_queue fetch
    (see scrape_from_queue_item._pre_fetch_gate) — root-caused 2026-08-26.
    """
    from app.modules.crawler.domain_tracker import (
        domain_crawl_budget_exhausted,
        domain_in_cooldown,
    )
    from app.modules.crawler.robots import is_allowed
    from app.modules.scraper.core.link_extractor import extract_page_links

    if domain_crawl_budget_exhausted(domain) or domain_in_cooldown(domain):
        return [], 0

    def _fetch(url: str) -> tuple[str, str]:
        cached = _cached_page_body(url)
        if cached is not None:
            return cached, ""
        if not is_allowed(url):
            return "", ""
        result = driver.scrape_with_fallback(url, domain)
        return result.text or "", result.raw_html or ""

    landing_text, landing_html = _fetch(landing_url)
    landing_same, landing_external = (
        extract_page_links(landing_html, landing_url, limit=_LINK_SCAN_LIMIT)
        if landing_html
        else ([], [])
    )
    same_domain_link_count = len(landing_same)
    pages = [(landing_url, landing_text, tuple(u for u, _ in landing_external))]
    if max_pages <= 1:
        return pages, same_domain_link_count

    candidate_urls = [u for u, _ in landing_same]
    if not candidate_urls:
        candidate_urls = [u for u in _cached_domain_urls(domain) if u != landing_url]

    for link_url in candidate_urls[: max_pages - 1]:
        try:
            text, html = _fetch(link_url)
        except Exception:
            continue
        if not text:
            continue
        _same, external = (
            extract_page_links(html, link_url, limit=_LINK_SCAN_LIMIT) if html else ([], [])
        )
        pages.append((link_url, text, tuple(u for u, _ in external)))
    return pages, same_domain_link_count


# Auto-generated "connect X with Y" integration-marketplace pages exist for
# nearly every popular SaaS product paired with nearly every other product —
# IFTTT's "quickly connect algorand blockchain to zoom" applet page is a
# templated listing, not evidence anyone built or uses that connection. A
# result HOSTED on one of these must never count as corroboration, no matter
# how well its blurb happens to pair the two names (root-caused 2026-07-26:
# zoom.us/mailchimp.com/notion.so/clickup.com/blink.sh all got approved off
# an IFTTT template page — the domains themselves have nothing to do with
# Algorand).
_AUTO_INTEGRATION_MARKETPLACES = frozenset(
    {"ifttt.com", "zapier.com", "make.com", "pipedream.com", "unito.io"}
)


def _external_corroboration(domain: str) -> tuple[str, str] | None:
    """Last resort after an in-domain crawl finds nothing: search SearXNG for "{domain} algorand" and check whether any of the top 20 results' OWN title+snippet pairs the service with "algorand" — not just that the search engine matched the query terms somewhere. This is exactly the pattern behind every real corroboration found by hand the night this was built: txnlab.dev's own link text named zerosignal.ai next to "Algorand", a Reddit post was literally titled "Welcome Sow & Reap to Algorand", a LinkedIn post named both together at the Algorand India Summit. None of those pages are on any curated "credible domain" list — the connection being stated in that specific result's own blurb is the signal, not where it's hosted (root-caused/added 2026-07-22).

    Returns (result_url, matched_snippet) on a hit, else None. Fails closed
    (no corroboration) on any search error — a SearXNG hiccup must degrade to
    the existing dead_end verdict, never crash the classify task.
    """
    from urllib.parse import urlparse

    from app.modules.ai.research_tools import _tool_search_web

    name_variants = {v for v in (domain.lower(), domain.split(".")[0].lower()) if v}
    try:
        result = _tool_search_web(query=f"{domain} algorand", limit=20)
    except Exception:
        return None
    for item in result.get("results") or []:
        result_url = str(item.get("url", ""))
        result_host = (urlparse(result_url).hostname or "").lower()
        if any(
            result_host == m or result_host.endswith(f".{m}")
            for m in _AUTO_INTEGRATION_MARKETPLACES
        ):
            continue
        blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        if "algorand" not in blob:
            continue
        if any(variant in blob for variant in name_variants):
            return result_url, blob[:300]
    return None


# deep_classify_domain's own hard-kill boundary is celery_app.conf.
# task_time_limit (1860s default) -- see reap_stale_deep_classify_flags'
# own comment below for why this reads that directly instead of adding a
# new config setting. A full FRONTIER_DEEP_CLASSIFY_MAX_PAGES crawl (200
# pages by default) run serially, each with a 0.3s politeness sleep plus a
# real fetch that can hit the ~35s BROWSER_TIMEOUT_MS Playwright SPA
# fallback, can run for thousands of seconds on a large/slow domain --
# structurally past task_time_limit -- and nothing is stored until
# _run_deep_classify's verdict at the very end, so a SIGKILL mid-crawl
# throws away 100% of the crawl work done so far (root-caused by the
# 2026-08-28 perf audit; the domain's deep_classify_queued flag is then
# left for reap_stale_deep_classify_flags to eventually notice and clear,
# instead of the run finishing normally). This margin is how much of
# task_time_limit the crawl LOOP below may spend before stopping itself
# early with whatever evidence it already has -- leaving room for the
# post-loop verdict work (one _external_corroboration SearXNG call, a
# couple of Cassandra writes, an enqueue) plus a cushion below celery's
# own task_soft_time_limit (1800s) too, so a slow domain degrades to an
# honest partial verdict (see _deep_crawl_for_relevance's exhaustive=False
# path below) instead of tripping SoftTimeLimitExceeded or the hard kill.
_DEEP_CLASSIFY_TIME_BUDGET_MARGIN_SECONDS = 300


def _deep_classify_time_budget_seconds() -> float:
    """How long _deep_crawl_for_relevance's own fetch loop may run before stopping itself early -- see _DEEP_CLASSIFY_TIME_BUDGET_MARGIN_SECONDS above. A plain function (not a module-level constant) so it always reflects celery_app.conf.task_time_limit at call time, the same reasoning reap_stale_deep_classify_flags' own default already follows."""
    return max(0.0, celery_app.conf.task_time_limit - _DEEP_CLASSIFY_TIME_BUDGET_MARGIN_SECONDS)


def _deep_crawl_should_stop(domain: str, deadline: float) -> bool:
    """Whether _deep_crawl_for_relevance's own loop should give up now with whatever evidence it already has, instead of popping another URL -- either the wall-clock deadline (monotonic seconds, see _deep_classify_time_budget_seconds) ran out, or the same per-domain crawl-budget/cooldown trip already fired. A separate top-level function (rather than inlining a 3-way `or` in the loop) purely to keep _deep_crawl_for_relevance's own branch count under CLAUDE.md's complexity guidance -- not a DI seam; the local import below mirrors this file's own established style for these same two domain_tracker functions (see _sample_domain_pages)."""
    import time

    from app.modules.crawler.domain_tracker import domain_crawl_budget_exhausted, domain_in_cooldown

    return (
        time.monotonic() >= deadline
        or domain_crawl_budget_exhausted(domain)
        or domain_in_cooldown(domain)
    )


def _deep_crawl_for_relevance(
    *, domain: str, landing_url: str, max_pages: int, time_budget_seconds: float | None = None
) -> tuple[tuple[str, object] | None, int, bool, int]:
    """Random-order same-domain crawl that stops at the first page clearing score_page's threshold.

    Returns (found, fetched, exhaustive, landing_same_domain_link_count) where
    found is (url, score_result) or None. `exhaustive` is True only when the
    frontier ran dry — every reachable same-domain page really was checked —
    versus stopping because max_pages was hit, or the wall-clock time budget
    (time_budget_seconds, default _deep_classify_time_budget_seconds() — see
    its docstring) ran out, with pages still unexplored. The time-budget stop
    deliberately reuses this exact same "budget hit, not exhaustive" shape
    instead of inventing a third outcome — to every downstream verdict branch
    in _run_deep_classify, a time-budget stop and a max_pages stop are the
    same kind of honest partial evidence.

    Each iteration re-checks the same domain-level gate scrape_from_queue_
    item's own _pre_fetch_gate applies to routine drain_url_queue fetches —
    domain_crawl_budget_exhausted (the per-domain page-budget cap) and
    domain_in_cooldown (diversity spacing) — before popping the next URL, and
    is_allowed (robots.txt) per URL as before. Neither of the first two was
    checked at all here previously (root-caused 2026-08-26): this crawl talks
    to WebCrawlerDriver.scrape_with_fallback directly, never through
    _pre_fetch_gate, so a 200-page deep-classify escalation could burn its
    entire budget against a domain regardless of that domain's page-budget or
    cooldown state. A domain-level trip BREAKS the loop (not `continue` — it
    applies to the whole domain, not just the popped URL), which surfaces the
    same way running out of max_pages does: exhaustive=False, since the
    frontier is generally still non-empty.
    """
    import random
    import time

    from app.modules.crawler.robots import is_allowed
    from app.modules.scraper.core.link_extractor import extract_page_links
    from app.modules.search.classifier.score import score_page

    driver = WebCrawlerDriver()
    visited: set[str] = set()
    frontier: list[str] = [landing_url]
    fetched = 0
    found: tuple[str, object] | None = None
    budget = (
        _deep_classify_time_budget_seconds() if time_budget_seconds is None else time_budget_seconds
    )
    deadline = time.monotonic() + budget

    # Full Site / Single Page suggestion signal (see suggest_full_site) — a
    # free by-product of the landing page's own link extraction below, which
    # this task already does as part of its normal crawl. Captured only on
    # the landing page itself, not later random-order fetches, since that's
    # the same "front door" signal _sample_domain_pages uses elsewhere. This
    # task deliberately stops at the first relevant page (see docstring), so
    # without this the "how many pages fetched" count would badly understate
    # a real site's size (root-caused 2026-07-26: quantozpay.com/opensea.io
    # both resolved here in 1 fetch and wrongly suggested Single Page).
    landing_same_domain_link_count = 0

    while frontier and fetched < max_pages:
        if _deep_crawl_should_stop(domain, deadline):
            break
        url = frontier.pop(random.randrange(len(frontier)))
        if url in visited or not is_allowed(url):
            continue
        visited.add(url)
        try:
            result = driver.scrape_with_fallback(url, domain)
        except Exception:
            continue
        fetched += 1
        if not result.text or len(result.text.strip()) < 100:
            continue
        same, external = (
            extract_page_links(result.raw_html, url, limit=_LINK_SCAN_LIMIT)
            if result.raw_html
            else ([], [])
        )
        if url == landing_url:
            landing_same_domain_link_count = len(same)
        score_result = score_page(
            url=url, text=result.text, outbound_links=tuple(u for u, _ in external)
        )
        if score_result.in_scope:
            found = (url, score_result)
            break
        for link_url, _anchor_text in same:
            if link_url not in visited:
                frontier.append(link_url)
        # This is a one-off bulk crawl of a single site, not routine traffic —
        # a small per-request gap keeps it well short of hammering the host
        # even at a couple hundred fetches.
        time.sleep(0.3)

    # Two different "found nothing" outcomes, and they carry different
    # confidence: the frontier running dry means every reachable same-domain
    # page was actually checked — as exhaustive a negative as this task can
    # produce. Stopping for any other reason (max_pages hit, or the time
    # budget above ran out) with the frontier still non-empty means there
    # were more unexplored pages when it stopped — a real negative signal,
    # but a budget limit, not proof the rest of the site has nothing either.
    return found, fetched, not frontier, landing_same_domain_link_count


def _clear_deep_classify_queued(domain: str) -> None:
    """Best-effort clear of the ``deep_classify_queued`` in-flight marker once a ``deep_classify_domain`` run has actually finished — success, reject, or an uncaught exception. ``update_domain_status``/``UPDATE_METADATA`` MERGE metadata, they never delete keys, so a value written "true" before dispatch (see ``_classify_and_store_domain`` and ``gray_zone_reconciliation.dispatch_gray_zone_deep_classify``) stays "true" forever unless something explicitly overwrites it — root-caused 2026-08-26 (see ``gray_zone_reconciliation.py``'s own module docstring): neither the approve, external-corroboration, nor reject branch here ever cleared it, so every completed run left the domain permanently excluded from ``_gray_zone_rows``'/``_classify_and_store_domain``'s own "already in flight" dedup check.

    Called from ``deep_classify_domain``'s own ``finally`` block (CLAUDE.md
    invariant 7: flags set before dispatch must be cleared on failure too),
    so it runs on every exit path that block reaches. The one path it can't
    reach is a hard SIGKILL past the task's own ``task_time_limit`` — that
    gap is what ``reap_stale_deep_classify_flags`` below is for. Fails open
    with a log line, not a raise: a write hiccup here must never mask
    whatever verdict (or exception) the run itself already produced.
    """
    from app.modules.crawler.domain_tracker import update_domain_status

    try:
        update_domain_status(domain, metadata={"deep_classify_queued": "false"})
    except Exception:
        logger.warning("failed to clear deep_classify_queued flag for %s", domain, exc_info=True)


@celery_app.task(name="app.tasks.crawler.deep_classify_domain")
def deep_classify_domain(
    *, domain: str, seed_url: str = "", max_pages: int = 200
) -> dict[str, object]:
    """One-time, thorough relevance verdict for a domain the cheap FRONTIER_CLASSIFY_SAMPLE_PAGES sample couldn't resolve — before rejecting a domain for good, actually look at up to max_pages of it instead of trusting a handful of homepage-linked pages to represent the whole site.

    Crawls in RANDOM order (not link/DOM order) from a frontier seeded at the
    landing page and grown as pages are visited, so it isn't stuck sampling
    only whatever branch of the site the landing page happens to link to
    first. Stops at the very first page that clears score_page's relevance
    threshold — a real hit usually resolves in a handful of fetches; only a
    genuinely off-topic domain pays the full max_pages cost, and that only
    happens once per domain, ever.

    If the in-domain crawl finds nothing, one last free check runs before
    committing to a reject: _external_corroboration searches SearXNG for
    outside confirmation (see its docstring) — a site can be entirely
    chain-silent about its own Algorand affiliation while the outside world
    (an ecosystem partner's own page, a community post) already states it.

    The verdict here is FINAL and PERMANENT: approved domains get
    frontier_status=approved same as any other approval; rejected ones get
    is_relevant=False + frontier_status=dead_end, which should_recrawl_domain
    treats as a permanent human-grade reject — this task must never be queued
    again for the same domain once it's decided (root-caused 2026-07-21:
    quantoz.com/EURQ is multi-chain and never says "algorand" in its own
    prose — a shallow same-page-hop sample can legitimately miss a domain
    that IS relevant, so a reject needs more evidence than that before it's
    treated as permanent).

    A rejection carries one of two different confidence levels, recorded in
    metadata as deep_classify_exhaustive: "true" means the frontier ran dry —
    every reachable same-domain page was actually checked, as conclusive a
    negative as this task can produce. "false" means either max_pages was hit
    or the crawl's own wall-clock time budget ran out (see
    _deep_classify_time_budget_seconds — a large/slow domain stops itself
    early, before this task's own celery hard time limit would SIGKILL it
    mid-crawl and discard everything) while pages were still unexplored —
    still a real negative signal (this is what actually gets stored either
    way), but a budget limit, not proof the rest of the site has nothing
    either; the note field says which, so a human reviewing the domain list
    can tell the two apart.

    The Celery task decorator belongs HERE, not on the ``_deep_crawl_for_
    relevance`` helper above (root-caused 2026-08-25): it used to sit on that
    helper under this same task name, so ``_classify_and_store_domain``'s
    ``send_task("app.tasks.crawler.deep_classify_domain", kwargs={"domain":
    ..., "seed_url": ..., "max_pages": ...})`` resolved to a function whose
    signature doesn't even take ``seed_url`` (it takes ``landing_url``) and,
    worse, never writes the verdict back to Cassandra at all — every real
    escalation would fail at the worker, leaving the domain's
    ``deep_classify_queued`` metadata flag permanently "true" and its
    ``frontier_status`` stuck at "pending" forever, since
    ``_classify_and_store_domain`` treats that flag as "already in flight"
    and no-ops on every later pass. The existing tests never caught this
    because they call ``deep_classify_domain(...)`` directly as a plain
    function, bypassing the Celery name resolution entirely.

    The whole crawl-and-verdict body runs inside a ``try``/``finally`` that
    calls ``_clear_deep_classify_queued`` on every exit — approve, external-
    corroboration approve, reject, or an uncaught exception — so a completed
    (or crashed-but-caught) run never leaves ``deep_classify_queued="true"``
    stuck on the domain forever (root-caused 2026-08-26: none of the three
    verdict branches below ever cleared it, so every finished run was
    permanently invisible to the next ``_classify_and_store_domain``/
    ``dispatch_gray_zone_deep_classify`` in-flight check). A hard SIGKILL
    past this task's own ``task_time_limit`` still skips the ``finally`` —
    see ``reap_stale_deep_classify_flags`` for that remaining gap.
    """
    landing_url = seed_url or f"https://{domain}"
    try:
        return _run_deep_classify(domain=domain, landing_url=landing_url, max_pages=max_pages)
    finally:
        _clear_deep_classify_queued(domain)


def _run_deep_classify(*, domain: str, landing_url: str, max_pages: int) -> dict[str, object]:
    """The actual crawl-and-verdict body of deep_classify_domain, split out so the try/finally wrapping it (see deep_classify_domain's own docstring) reads as one clean block instead of an indented 150+-line body -- CLAUDE.md's 150-line-function guidance."""
    from app.modules.crawler.domain_tracker import (
        ensure_monitored_service,
        suggest_full_site,
        update_domain_status,
    )

    found, fetched, exhaustive, landing_same_domain_link_count = _deep_crawl_for_relevance(
        domain=domain, landing_url=landing_url, max_pages=max_pages
    )

    if found is not None:
        found_url, score_result = found
        # relevance_score is deliberately omitted (preserved as-is): it's the
        # keyword-scale ~0-10 per-page signal, not this 0-1 verdict, which
        # lives in metadata's content_relevance below (see update_domain_
        # status's docstring — root-caused 2026-08-25).
        update_domain_status(
            domain,
            is_relevant=True,
            online=True,
            frontier_status_override="approved",
            metadata={
                "frontier_status": "approved",
                "content_relevance": f"{score_result.score:.3f}",
                "content_relevance_reasons": "; ".join(score_result.reasons),
                # Structured counterpart of content_relevance_reasons above —
                # each fired signal's actual numeric contribution, for the
                # admin Domains tab's relevance breakdown (see
                # ClassifierResult.components' own docstring). JSON-encoded
                # since domain_tracking.metadata is a Cassandra
                # map<text, text>, not a nested-map column.
                "content_relevance_components": json.dumps(score_result.components),
                "content_relevance_url": found_url,
                "deep_classified": "true",
                "deep_classify_pages_fetched": str(fetched),
                "suggested_full_site": (
                    "true" if suggest_full_site(domain, landing_same_domain_link_count) else "false"
                ),
                "same_domain_link_count": str(landing_same_domain_link_count),
            },
        )
        # An automated approve is a full approve — mirror the discovery-time
        # auto-approve in link_extractor.py (_process_external_link) and
        # register the monitored source too, or this domain gets crawled into
        # the research corpus forever without ever reaching the publish queue
        # (exactly the gap ensure_monitored_service's own docstring documents
        # for the discovery path, reintroduced here since this task was added
        # after that fix and was never wired to it — root-caused 2026-08-25).
        ensure_monitored_service(domain, scrape_url=found_url)
        # Feed found_url into the ordinary crawl frontier too (root-caused
        # 2026-08-26, ulam.io): when a curated ecosystem-directory sync
        # (ecosystem_sync.py) already registered this domain's service
        # pointed at the bare landing page BEFORE this task ran,
        # ensure_monitored_service above is a no-op ("never overwrites an
        # existing row") — the one page that actually proves the domain's
        # relevance is then recorded only as a metadata pointer
        # (content_relevance_url) and never fetched again. The service's
        # aggregated context (service_context.build_service_context) is built
        # entirely from `crawled_pages_by_domain`, which this task never
        # writes to (`_deep_crawl_for_relevance` fetches pages through its
        # own throwaway in-memory crawl, not the queue-backed frontier) — so
        # without this, the confirmed-relevant page is discovered once and
        # then permanently invisible to every future aggregate/priority
        # computation, which falls back to whatever thin/off-topic content
        # the landing page itself happens to have. Enqueuing here is what
        # actually gets it fetched-and-stored via the normal
        # web_crawler.scrape_from_queue_item -> index_crawled_page path.
        # Harmless when ensure_monitored_service DID adopt found_url as the
        # entry URL too (enqueue_url's own pending-dedup makes a repeat
        # enqueue a no-op). Best-effort: a queueing failure here must not
        # fail the classification verdict that was already persisted above.
        try:
            from app.modules.crawler.url_queue import enqueue_url

            enqueue_url(found_url, source="deep_classify_relevant_page", priority=20)
        except Exception:
            logger.warning(
                "failed to enqueue deep-classify relevant page %s", found_url, exc_info=True
            )
        return {
            "status": "ok",
            "domain": domain,
            "verdict": "approved",
            "pages_fetched": fetched,
            "found_at": found_url,
            "score": score_result.score,
        }

    # Last resort before a permanent reject: the site itself may be
    # chain-silent even after checking everything reachable on it (quantoz's
    # own gap, plus zerosignal.ai/dark-coin.com/sowandreap.in — none of which
    # state their Algorand affiliation on their own pages at all). One free
    # SearXNG query checks whether the outside world already corroborates it.
    corroboration = _external_corroboration(domain)
    if corroboration is not None:
        corrob_url, corrob_snippet = corroboration
        # relevance_score omitted — see the comment on the approve branch above.
        update_domain_status(
            domain,
            is_relevant=True,
            online=True,
            frontier_status_override="approved",
            metadata={
                "frontier_status": "approved",
                "content_relevance": "0.450",
                "content_relevance_reasons": f"external_corroboration:{corrob_url}",
                "content_relevance_url": corrob_url,
                "deep_classified": "true",
                "deep_classify_pages_fetched": str(fetched),
                "external_corroboration_snippet": corrob_snippet,
                "suggested_full_site": (
                    "true" if suggest_full_site(domain, landing_same_domain_link_count) else "false"
                ),
                "same_domain_link_count": str(landing_same_domain_link_count),
            },
        )
        # Same as the in-domain approve above: an automated approve must
        # still register the monitored source. corrob_url is an OUTSIDE page
        # (a partner's post, a forum thread) that isn't on this domain, so
        # the service points at the domain's own landing page, not the
        # corroborating URL.
        ensure_monitored_service(domain, scrape_url=landing_url)
        return {
            "status": "ok",
            "domain": domain,
            "verdict": "approved",
            "pages_fetched": fetched,
            "found_at": corrob_url,
            "score": 0.45,
            "via": "external_corroboration",
        }

    # relevance_score omitted — see the comment on the approve branch above.
    update_domain_status(
        domain,
        is_relevant=False,
        online=True,
        frontier_status_override="dead_end",
        metadata={
            "frontier_status": "dead_end",
            "deep_classified": "true",
            "deep_classify_pages_fetched": str(fetched),
            "deep_classify_exhaustive": "true" if exhaustive else "false",
            "external_corroboration_checked": "true",
            "note": (
                (
                    f"deep-crawled the entire reachable site ({fetched} pages, "
                    "frontier exhausted), no Algorand reference found"
                    if exhaustive
                    else (
                        f"deep-crawled {fetched} pages (budget limit reached, "
                        "more unexplored pages remained), no Algorand reference "
                        "found — a bigger budget could still find something"
                    )
                )
                + " (2026-07-21)"
            ),
        },
    )
    return {
        "status": "ok",
        "domain": domain,
        "verdict": "dead_end",
        "pages_fetched": fetched,
        "exhaustive": exhaustive,
    }


def _mark_unscoreable(
    session: CassandraSession, domain: str, meta: dict, url: str, reason: str, *, dry_run: bool
) -> None:
    """A domain whose sample fetch raised or came back unreadable must still get a metadata write, or it never leaves the 'unscored' sort bucket and gets retried on EVERY future classify_pending_domains pass forever — a permanently-broken domain (DNS failure, dead site) would otherwise be hammered on every batch indefinitely (root-caused 2026-07-22: this, not literal duplicate fetches, was the main driver of "recrawling the same pages" — busk.my/bwarelabs.com/germanblockchainweek.com kept reappearing in every batch). Deliberately does NOT go through the reject/escalate path — a fetch failure isn't an off-topic verdict, so it must never trigger a 200-page deep_classify_domain crawl of a domain whose landing page doesn't even resolve."""
    if dry_run:
        return
    from app.core.statements import DomainTrackingStmts

    new_meta = {
        **meta,
        "content_relevance": "0.000",
        "content_relevance_reasons": reason,
        "content_relevance_url": url,
    }
    session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, domain))


def _pending_domains_to_classify(session: CassandraSession, limit: int) -> list[tuple[str, dict]]:
    """Domains with frontier_status=pending, unscored ones first (the LIST scan returns token order, so without this a periodic caller re-scores the same first slice forever and the rest of the pool never gets a content score), capped to limit."""
    from app.core.statements import DomainTrackingStmts

    rows = session.execute(DomainTrackingStmts.LIST, (limit * 30,))
    pending = []
    for r in rows:
        meta = dict(r.metadata or {})
        status = r.frontier_status or meta.get("frontier_status")
        if status == "pending":
            pending.append((r.domain, meta))
    pending.sort(key=lambda item: "content_relevance" in item[1])
    return pending[:limit]


def _best_scored_page(pages: list[tuple[str, str, list]]) -> tuple[str, ClassifierResult]:
    """The best-scoring sampled page and its score_page() result. A chain-silent service's homepage can score 0 while a deeper page scores well; one bad page must not sink a domain the sample otherwise shows is relevant."""
    from app.modules.search.classifier.score import score_page

    best_url, best_text, best_links = pages[0]
    best_result = score_page(url=best_url, text=best_text, outbound_links=best_links)
    for page_url, page_text, page_links in pages[1:]:
        candidate = score_page(url=page_url, text=page_text, outbound_links=page_links)
        if candidate.score > best_result.score:
            best_result, best_url = candidate, page_url
    return best_url, best_result


def _classify_and_store_domain(
    session: CassandraSession,
    domain: str,
    meta: dict,
    url: str,
    score: float,
    score_result: ClassifierResult,
    best_url: str,
    *,
    will_reject: bool,
    same_domain_link_count: int = 0,
) -> str:
    """Write the classification back and apply the reject/escalate/keep-pending decision. Returns 'escalated' | 'rejected' | 'pending' ('pending' also covers the deep-classify-already-in-flight no-op, which makes no state change)."""
    from datetime import UTC, datetime

    from app.core.config import FRONTIER_DEEP_CLASSIFY_ENABLED, FRONTIER_DEEP_CLASSIFY_MAX_PAGES
    from app.core.statements import DomainTrackingStmts
    from app.modules.crawler.domain_tracker import suggest_full_site, update_domain_status

    new_meta = {
        **meta,
        "content_relevance": f"{score:.3f}",
        # Why the score landed where it did — shown next to the score
        # chip in the admin Domains tab; previously computed by
        # score_page() and thrown away, so a reviewer had a number
        # with no explanation (owner feedback 2026-07-12).
        "content_relevance_reasons": "; ".join(score_result.reasons),
        # Structured counterpart of content_relevance_reasons above — see
        # the same note on deep_classify_domain's approve branch.
        "content_relevance_components": json.dumps(score_result.components),
        # Which sampled page actually produced the score — no longer
        # always the landing page now that a domain is judged on its
        # best page, not just the first one.
        "content_relevance_url": best_url,
        # Full Site / Single Page reviewer nudge — advisory only, see
        # suggest_full_site's docstring. Computed here (not at review-render
        # time) since it's a free by-product of the sampling already done for
        # content_relevance above.
        "suggested_full_site": "true"
        if suggest_full_site(domain, same_domain_link_count)
        else "false",
        "same_domain_link_count": str(same_domain_link_count),
    }
    if will_reject and FRONTIER_DEEP_CLASSIFY_ENABLED:
        # The shallow sample's reject is a lead, not a verdict — before
        # rejecting for good, escalate to a thorough one-time crawl
        # (deep_classify_domain) instead of trusting a handful of
        # homepage-linked pages to represent the whole site. Dedup on
        # deep_classify_queued so a still-in-flight domain doesn't get
        # re-queued every time this task runs.
        if meta.get("deep_classify_queued") == "true":
            return "pending"
        new_meta["deep_classify_queued"] = "true"
        # Stamped here too, not just in gray_zone_reconciliation.py's own
        # dispatch path -- reap_stale_deep_classify_flags below needs this on
        # EVERY escalation, whichever path queued it, or a domain escalated
        # from here (the ordinary shallow-sample reject path, not the
        # gray-zone one) can get stuck deep_classify_queued="true" forever
        # with no timestamp to judge staleness by if the dispatched task never
        # runs to completion (dropped message, crashed worker -- see
        # deep_classify_domain's own try/finally and _clear_deep_classify_
        # queued for the normal-exit clear this covers the gap for).
        new_meta["deep_classify_queued_at"] = datetime.now(tz=UTC).isoformat()
        new_meta["frontier_status"] = "pending"
        session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, domain))
        from app.celery_app import celery_app as _celery_app

        _celery_app.send_task(
            "app.tasks.crawler.deep_classify_domain",
            kwargs={
                "domain": domain,
                "seed_url": url,
                "max_pages": FRONTIER_DEEP_CLASSIFY_MAX_PAGES,
            },
            queue="scrape",
        )
        return "escalated"
    if will_reject:
        new_meta["frontier_status"] = "dead_end"
        new_meta["auto_rejected"] = "content_off_topic"
        # relevance_score omitted (preserved as-is) — new_meta["content_relevance"]
        # above already carries this 0-1 verdict; the column stays on its
        # keyword scale (see update_domain_status's docstring).
        update_domain_status(
            domain,
            is_relevant=False,
            online=True,
            metadata=new_meta,
            frontier_status_override="dead_end",
        )
        return "rejected"
    new_meta["frontier_status"] = "pending"
    session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, domain))
    return "pending"


@celery_app.task(name="app.tasks.crawler.classify_pending_domains")
def classify_pending_domains(
    *, limit: int = 40, dry_run: bool = True, auto_reject: bool = False
) -> dict[str, object]:
    """Content-based domain relevance: crawl each pending domain's landing page (plus a same-domain link sample — see _sample_domain_pages and FRONTIER_CLASSIFY_SAMPLE_PAGES), classify the REAL page text (not the <head> preview that wrongly blocked pact.fi etc.), take the best-scoring sampled page as the domain's relevance, store it, and OPTIONALLY auto-reject only the clearly off-topic ones. Safe by default: auto_reject=False so the scores can be validated first; protected domains are never auto-rejected.

    The task decorator belongs on this function, not on the small
    ``_pending_domains_to_classify`` fetch-helper above — it used to sit
    there under this same task name (same misplaced-decorator bug as
    ``deep_classify_domain``, root-caused 2026-08-25), which would have made
    any future ``send_task("app.tasks.crawler.classify_pending_domains", ...)``
    call resolve to a two-positional-argument helper instead of the real
    classify pipeline. Not currently reachable in production (this pipeline
    is only ever invoked in-process — by ``reevaluate_pending_domains``'s
    daily beat call, and directly in tests) but a live trap for the next
    caller that dispatches it by name expecting the documented behavior.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.config import FRONTIER_CLASSIFY_SAMPLE_PAGES, FRONTIER_CONTENT_REJECT_SCORE
    from app.modules.crawler.domain_tracker import is_protected_domain
    from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver

    session = get_cassandra_session()
    pending = _pending_domains_to_classify(session, limit)

    driver = WebCrawlerDriver()
    scored = rejected = errors = unreadable = escalated = 0
    samples: list[dict] = []
    for domain, meta in pending:
        url = meta.get("pending_url") or f"https://{domain}"
        try:
            # HTTP first, transparent Playwright fallback for thin/SPA pages, so a
            # JS dApp gets its REAL rendered text instead of mis-scoring 0.
            pages, same_domain_link_count = _sample_domain_pages(
                driver, url, domain, FRONTIER_CLASSIFY_SAMPLE_PAGES
            )
        except Exception:
            errors += 1
            _mark_unscoreable(session, domain, meta, url, "fetch_error", dry_run=dry_run)
            continue
        # Too little text to judge (dead/blocked/SPA-disabled) — don't pretend
        # it's off-topic; leave it for a human.
        pages = [(u, t, links) for u, t, links in pages if len(t.strip()) >= 100]
        if not pages:
            unreadable += 1
            _mark_unscoreable(session, domain, meta, url, "unreadable", dry_run=dry_run)
            continue

        best_url, score_result = _best_scored_page(pages)
        score = round(float(score_result.score), 3)
        scored += 1
        will_reject = (
            auto_reject
            and score < FRONTIER_CONTENT_REJECT_SCORE
            and not is_protected_domain(domain)
        )
        if len(samples) < 40:
            samples.append({"domain": domain, "score": score, "reject": will_reject})
        if not dry_run:
            outcome = _classify_and_store_domain(
                session,
                domain,
                meta,
                url,
                score,
                score_result,
                best_url,
                will_reject=will_reject,
                same_domain_link_count=same_domain_link_count,
            )
            if outcome == "escalated":
                escalated += 1
            elif outcome == "rejected":
                rejected += 1
    samples.sort(key=lambda s: s["score"])
    return {
        "status": "ok",
        "dry_run": dry_run,
        "auto_reject": auto_reject,
        "scored": scored,
        "rejected": rejected,
        "escalated_to_deep_classify": escalated,
        "errors": errors,
        "unreadable": unreadable,
        "reject_threshold": FRONTIER_CONTENT_REJECT_SCORE,
        "samples_low_to_high": samples,
    }


@celery_app.task(name="app.tasks.crawler.retrain_publish_classifier")
def retrain_publish_classifier_task() -> dict[str, object]:
    """Celery task: retrain the sklearn publish classifier on latest labeled data."""
    # A second sklearn "learned grader" (grader_model.train_grader) used to run
    # here too, but its output had no live reader and was removed. The would-be
    # ModernBERT gatekeeper quality-head replacement (app.tasks.gatekeeper.
    # train_quality_head) was itself confirmed dead the same way — its checkpoint
    # had no serving path either — and was removed 2026-08-25. This task is the
    # sole retrain path admin_retrain queues today.
    from app.modules.ai.publish_classifier import retrain_publish_classifier

    classifier = retrain_publish_classifier()
    return {"classifier": classifier}


@celery_app.task(name="app.tasks.crawler.sync_ecosystem_directories")
def sync_ecosystem_directories_task() -> dict[str, object]:
    """Daily beat: ingest curated ecosystem directories (awesome-algorand etc.) and case-study indexes (algorand.co/case-studies), approving + monitoring listed/subject domains — the discovery path for chain-silent services and institutional users whose own sites score 0 relevance.

    See ecosystem_sync.
    """
    from app.core.config import ECOSYSTEM_SYNC_ENABLED
    from app.modules.crawler.ecosystem_sync import (
        sync_ecosystem_case_studies,
        sync_ecosystem_directories,
    )

    if not ECOSYSTEM_SYNC_ENABLED:
        return {"status": "skipped", "reason": "ecosystem_sync_disabled"}
    from app.core.config import ECOSYSTEM_API_SOURCES_ENABLED
    from app.modules.crawler.ecosystem_sync import sync_ecosystem_apis

    out: dict[str, object] = {
        "directories": sync_ecosystem_directories(),
        "case_studies": sync_ecosystem_case_studies(),
    }
    if ECOSYSTEM_API_SOURCES_ENABLED:
        out["apis"] = sync_ecosystem_apis()
    return out


@celery_app.task(name="app.tasks.crawler.discover_from_mentions")
def discover_from_mentions_task() -> dict[str, object]:
    """Daily beat: enqueue project URLs mentioned on GitHub (topic:algorand homepages) and the Medium algorand tag feed into the crawl frontier."""
    from app.core.config import MENTION_DISCOVERY_ENABLED
    from app.modules.crawler.mention_discovery import discover_from_mentions

    if not MENTION_DISCOVERY_ENABLED:
        return {"status": "skipped", "reason": "mention_discovery_disabled"}
    return discover_from_mentions()


@celery_app.task(name="app.tasks.crawler.reevaluate_pending_domains")
def reevaluate_pending_domains(*, limit: int = 40) -> dict[str, object]:
    """Daily retro-pass over the pending frontier pool: refresh content scores (unscored domains first), then PROMOTE any pending domain whose crawled- content relevance clears FRONTIER_CONTENT_PROMOTE_SCORE — approval only, never rejection. Fixes the one-shot nature of discovery-time auto-approve: pending rows never re-evaluated themselves, so domains that arrived before today's gates (criptomedia sat at content_relevance with no reader) or whose sites grew a real product later stayed buried forever."""
    from app.core.cassandra import get_cassandra_session
    from app.core.config import (
        FRONTIER_CONTENT_PROMOTE_SCORE,
        FRONTIER_RETRO_PROMOTE_ENABLED,
    )
    from app.core.statements import DomainTrackingStmts
    from app.modules.crawler.domain_tracker import ensure_monitored_service, update_domain_status
    from app.modules.crawler.url_queue import enqueue_url

    if not FRONTIER_RETRO_PROMOTE_ENABLED:
        return {"status": "skipped", "reason": "retro_promote_disabled"}

    classified = classify_pending_domains(limit=limit, dry_run=False, auto_reject=False)

    session = get_cassandra_session()
    promoted: list[str] = []
    for r in session.execute(DomainTrackingStmts.LIST, (5000,)):
        meta = dict(r.metadata or {})
        status = r.frontier_status or meta.get("frontier_status")
        if status != "pending" or r.is_relevant is False:
            continue
        try:
            score = float(meta.get("content_relevance", ""))
        except (TypeError, ValueError):
            continue
        if score < FRONTIER_CONTENT_PROMOTE_SCORE:
            continue
        # relevance_score omitted (preserved as-is) — metadata's content_relevance
        # (read above as `score`) already carries this 0-1 verdict; the column
        # stays on its keyword scale (see update_domain_status's docstring).
        update_domain_status(
            r.domain,
            is_relevant=True,
            online=True,
            frontier_status_override="approved",
            metadata={"frontier_status": "approved", "promoted_by": "content_retro"},
        )
        # Approval alone only unblocks future link-follows — queue the site
        # itself so it actually gets harvested now.
        seed_url = meta.get("pending_url") or f"https://{r.domain}"
        enqueue_url(seed_url, source="retro-promote", priority=40)
        # And register the monitored source, same as every other automated
        # approve path (discovery-time auto-approve in link_extractor.py,
        # deep_classify_domain above) — without it this domain gets crawled
        # into the research corpus by the retro-pass forever but can never
        # produce a publish candidate (root-caused 2026-08-25: this task was
        # added after ensure_monitored_service already existed to fix that
        # exact gap for the discovery path, but was never wired to it).
        ensure_monitored_service(r.domain, scrape_url=seed_url)
        promoted.append(r.domain)

    return {
        "status": "ok",
        "classified": {k: classified.get(k) for k in ("scored", "errors", "unreadable")},
        "promoted": len(promoted),
        "promoted_domains": promoted[:40],
        "threshold": FRONTIER_CONTENT_PROMOTE_SCORE,
    }


@celery_app.task(name="app.tasks.crawler.reclassify_gray_zone_domains")
def reclassify_gray_zone_domains(*, limit: int | None = None) -> dict[str, object]:
    """Small, throttled companion to reevaluate_pending_domains above, but for the OTHER frontier bucket: domains already frontier_status="approved" whose content_relevance never actually cleared FRONTIER_CONTENT_PROMOTE_SCORE (the 2026-08-26 665-domain gray-zone audit — see gray_zone_reconciliation.py's module docstring for the full picture). reevaluate_pending_domains only ever scans frontier_status="pending" rows, so it never touches these.

    Gated on FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED (default off) in addition
    to whatever gates the beat schedule entry itself — a defense-in-depth
    no-op if this task is ever triggered directly (e.g. by name from an
    admin shell) before the feature has been deliberately turned on. When
    enabled, dispatches at most `limit` (default
    FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT, deliberately small) domains per
    call to the real deep_classify_domain task — never runs the crawl
    itself, see dispatch_gray_zone_deep_classify's own docstring for why
    that matters here specifically (every dispatch is a real network crawl,
    unlike the cheap read-mostly sweeps elsewhere on this schedule).
    """
    from app.core.config import (
        FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED,
        FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT,
    )
    from app.modules.crawler.gray_zone_reconciliation import dispatch_gray_zone_deep_classify

    if not FRONTIER_GRAY_ZONE_RECLASSIFY_ENABLED:
        return {"status": "skipped", "reason": "gray_zone_reclassify_disabled"}

    effective_limit = FRONTIER_GRAY_ZONE_RECLASSIFY_LIMIT if limit is None else limit
    return dispatch_gray_zone_deep_classify(limit=effective_limit, dry_run=False)


@celery_app.task(name="app.tasks.crawler.reclaim_stale_processing_urls")
def reclaim_stale_processing_urls_task() -> dict[str, object]:
    """Maintenance beat: reset any url_queue row stuck in status='processing' for more than url_queue.STALE_PROCESSING_SECONDS (30 min) back to pending, so a worker that died mid-fetch (hard time_limit SIGKILL, a deploy's cold-shutdown SIGQUIT, an orphaned process) doesn't strand the row forever -- dequeue_url() hands a row to exactly one worker and nothing else ever un-sticks a stuck one. See url_queue.reclaim_stale_processing_urls for the mechanism (Redis-tracked processing-start markers, since url_queue itself has no per-row "started processing" timestamp column)."""
    return reclaim_stale_processing_urls()


# How long a domain_tracking row may sit with deep_classify_queued="true"
# before reap_stale_deep_classify_flags below treats it as abandoned rather
# than genuinely in flight. deep_classify_domain's own try/finally (see
# _clear_deep_classify_queued) already clears the flag on every exit path it
# reaches -- approve, external-corroboration approve, reject, or an uncaught
# exception -- so this reaper only ever catches the one thing a try/finally
# structurally can't: a hard SIGKILL past the task's own task_time_limit (the
# celery-wide hard kill; deep_classify_domain has no per-task override), a
# dropped Celery message, or a crashed worker pool. Mirrors COMPOSE_SESSION_
# STALE_MINUTES' own reasoning (workers/app/core/config.py) -- roughly 2x the
# hard time limit is a generous margin above any run that actually finished,
# while still catching a genuinely abandoned one the same day. Derived from
# celery_app.conf.task_time_limit directly (same source drain_url_queue's
# own single_flight TTL already reads) rather than a new config setting, so
# this stays self-contained to this module.
_DEEP_CLASSIFY_STALE_SECONDS_MULTIPLIER = 2


def reap_stale_deep_classify_flags(
    *, stale_seconds: int | None = None, scan_limit: int = 5000
) -> dict[str, object]:
    """Beat maintenance: clear deep_classify_queued="true" on any domain_tracking row whose deep_classify_queued_at is older than stale_seconds, so a deep_classify_domain run that never reached its own try/finally doesn't leave a domain permanently excluded from the next classify_pending_domains / dispatch_gray_zone_deep_classify in-flight check (see _clear_deep_classify_queued and this module's own _DEEP_CLASSIFY_STALE_SECONDS_MULTIPLIER comment for why a try/finally alone isn't enough).

    Mirrors url_queue.reclaim_stale_processing_urls' shape: a periodic sweep
    that RE-CHECKS the real current state (the flag itself) before writing,
    so a domain a completed run already resolved is never double-touched --
    only genuinely stuck rows get reset.

    deep_classify_queued_at is stamped at dispatch time by BOTH escalation
    paths that set deep_classify_queued="true" -- _classify_and_store_
    domain's ordinary shallow-sample escalation and gray_zone_reconciliation.
    dispatch_gray_zone_deep_classify's gray-zone dispatch -- so this one sweep
    covers rows from either. A row with deep_classify_queued="true" but no
    parseable deep_classify_queued_at (written before this field existed, or
    a malformed value) is skipped, not guessed at -- there is no way to judge
    staleness without a timestamp, mirroring find_stale_gray_zone_dispatches'
    own same choice in gray_zone_reconciliation.py.

    scan_limit bounds the domain_tracking table scan, matching the 5000-row
    scan reevaluate_pending_domains and gray_zone_reconciliation already use.
    """
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    effective_stale_seconds = (
        stale_seconds
        if stale_seconds is not None
        else celery_app.conf.task_time_limit * _DEEP_CLASSIFY_STALE_SECONDS_MULTIPLIER
    )
    session = get_cassandra_session()
    now = datetime.now(tz=UTC)

    reaped: list[str] = []
    skipped_no_timestamp = 0
    for row in session.execute(DomainTrackingStmts.LIST, (scan_limit,)):
        meta = dict(row.metadata or {})
        if meta.get("deep_classify_queued") != "true":
            continue
        queued_at_raw = meta.get("deep_classify_queued_at")
        if not queued_at_raw:
            skipped_no_timestamp += 1
            continue
        try:
            queued_at = datetime.fromisoformat(queued_at_raw)
        except (TypeError, ValueError):
            skipped_no_timestamp += 1
            continue
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=UTC)
        if (now - queued_at).total_seconds() < effective_stale_seconds:
            continue
        new_meta = {**meta, "deep_classify_queued": "false"}
        session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, row.domain))
        reaped.append(row.domain)

    return {
        "status": "ok",
        "reaped": len(reaped),
        "reaped_domains": reaped[:40],
        "skipped_no_timestamp": skipped_no_timestamp,
        "stale_seconds": effective_stale_seconds,
    }


@celery_app.task(name="app.tasks.crawler.reap_stale_deep_classify_flags")
def reap_stale_deep_classify_flags_task() -> dict[str, object]:
    """Maintenance beat: clear deep_classify_queued flags stuck 'true' well past deep_classify_domain's own task_time_limit -- the try/finally in deep_classify_domain (_clear_deep_classify_queued) covers every exit path it reaches, but a hard SIGKILL past the task's own hard time limit skips it, the same gap COMPOSE_SESSION_STALE_MINUTES/reap_stale_compose_sessions covers for compose_sessions. See reap_stale_deep_classify_flags for the mechanism."""
    return reap_stale_deep_classify_flags()
