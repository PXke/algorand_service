"""Celery tasks that drain the URL frontier queue and classify domains."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.celery_app import celery_app
from app.core.config import URL_QUEUE_ENABLED
from app.modules.crawler.url_queue import dequeue_url, pending_url_count
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession

    from app.modules.search.classifier.score import ClassifierResult


@celery_app.task(name="app.tasks.crawler.drain_url_queue")
def drain_url_queue(*, max_items: int = 5) -> dict[str, object]:
    """Dequeue URLs and run the web discovery pipeline."""
    if not URL_QUEUE_ENABLED:
        return {"status": "skipped", "reason": "url_queue_disabled", "processed": 0}

    driver = WebCrawlerDriver()
    results: list[dict[str, object]] = []
    processed = 0
    for _ in range(max_items):
        item = dequeue_url()
        if item is None:
            break
        outcome = driver.scrape_from_queue_item(item)
        results.append(outcome)
        processed += 1

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
    """
    from app.modules.scraper.core.link_extractor import extract_page_links

    def _fetch(url: str) -> tuple[str, str]:
        cached = _cached_page_body(url)
        if cached is not None:
            return cached, ""
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


def _external_corroboration(domain: str) -> tuple[str, str] | None:
    """Last resort after an in-domain crawl finds nothing: search SearXNG for "{domain} algorand" and check whether any of the top 20 results' OWN title+snippet pairs the service with "algorand" — not just that the search engine matched the query terms somewhere. This is exactly the pattern behind every real corroboration found by hand the night this was built: txnlab.dev's own link text named zerosignal.ai next to "Algorand", a Reddit post was literally titled "Welcome Sow & Reap to Algorand", a LinkedIn post named both together at the Algorand India Summit. None of those pages are on any curated "credible domain" list — the connection being stated in that specific result's own blurb is the signal, not where it's hosted (root-caused/added 2026-07-22).

    Returns (result_url, matched_snippet) on a hit, else None. Fails closed
    (no corroboration) on any search error — a SearXNG hiccup must degrade to
    the existing dead_end verdict, never crash the classify task.
    """
    from app.modules.ai.research_tools import _tool_search_web

    name_variants = {v for v in (domain.lower(), domain.split(".")[0].lower()) if v}
    try:
        result = _tool_search_web(query=f"{domain} algorand", limit=20)
    except Exception:
        return None
    for item in result.get("results") or []:
        blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        if "algorand" not in blob:
            continue
        if any(variant in blob for variant in name_variants):
            return str(item.get("url", "")), blob[:300]
    return None


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
    negative as this task can produce. "false" means max_pages was hit while
    pages were still unexplored — still a real negative signal (this is what
    actually gets stored either way), but a budget limit, not proof the rest
    of the site has nothing either; the note field says which, so a human
    reviewing the domain list can tell the two apart.
    """
    import random
    import time

    from app.modules.crawler.domain_tracker import update_domain_status
    from app.modules.crawler.robots import is_allowed
    from app.modules.scraper.core.link_extractor import extract_page_links
    from app.modules.search.classifier.score import score_page

    landing_url = seed_url or f"https://{domain}"
    driver = WebCrawlerDriver()
    visited: set[str] = set()
    frontier: list[str] = [landing_url]
    fetched = 0
    found: tuple[str, object] | None = None

    while frontier and fetched < max_pages:
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
    # produce. Hitting max_pages with the frontier still non-empty means there
    # were more unexplored pages when it stopped — a real negative signal,
    # but a budget limit, not proof the rest of the site has nothing either.
    exhaustive = not frontier

    if found is not None:
        found_url, score_result = found
        update_domain_status(
            domain,
            relevance_score=score_result.score,
            is_relevant=True,
            online=True,
            frontier_status_override="approved",
            metadata={
                "frontier_status": "approved",
                "content_relevance": f"{score_result.score:.3f}",
                "content_relevance_reasons": "; ".join(score_result.reasons),
                "content_relevance_url": found_url,
                "deep_classified": "true",
                "deep_classify_pages_fetched": str(fetched),
            },
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
        update_domain_status(
            domain,
            relevance_score=0.45,
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
            },
        )
        return {
            "status": "ok",
            "domain": domain,
            "verdict": "approved",
            "pages_fetched": fetched,
            "found_at": corrob_url,
            "score": 0.45,
            "via": "external_corroboration",
        }

    update_domain_status(
        domain,
        relevance_score=0.0,
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


@celery_app.task(name="app.tasks.crawler.classify_pending_domains")
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
        # Which sampled page actually produced the score — no longer
        # always the landing page now that a domain is judged on its
        # best page, not just the first one.
        "content_relevance_url": best_url,
        # Full Site / Single Page reviewer nudge — advisory only, see
        # suggest_full_site's docstring. Computed here (not at review-render
        # time) since it's a free by-product of the sampling already done for
        # content_relevance above.
        "suggested_full_site": "true" if suggest_full_site(domain, same_domain_link_count) else "false",
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
        update_domain_status(
            domain,
            relevance_score=score,
            is_relevant=False,
            online=True,
            metadata=new_meta,
            frontier_status_override="dead_end",
        )
        return "rejected"
    new_meta["frontier_status"] = "pending"
    session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, domain))
    return "pending"


def classify_pending_domains(
    *, limit: int = 40, dry_run: bool = True, auto_reject: bool = False
) -> dict[str, object]:
    """Content-based domain relevance: crawl each pending domain's landing page (plus a same-domain link sample — see _sample_domain_pages and FRONTIER_CLASSIFY_SAMPLE_PAGES), classify the REAL page text (not the <head> preview that wrongly blocked pact.fi etc.), take the best-scoring sampled page as the domain's relevance, store it, and OPTIONALLY auto-reject only the clearly off-topic ones. Safe by default: auto_reject=False so the scores can be validated first; protected domains are never auto-rejected."""
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
    # The sklearn "learned grader" (grader_model.train_grader) used to run here
    # too, but its output has no live reader — the gatekeeper quality head
    # replaces it (see app.tasks.gatekeeper.train_quality_head, queued
    # separately by admin_retrain since it's a much heavier CPU job).
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
    from app.modules.crawler.domain_tracker import update_domain_status
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
        update_domain_status(
            r.domain,
            relevance_score=score,
            is_relevant=True,
            online=True,
            frontier_status_override="approved",
            metadata={"frontier_status": "approved", "promoted_by": "content_retro"},
        )
        # Approval alone only unblocks future link-follows — queue the site
        # itself so it actually gets harvested now.
        enqueue_url(
            meta.get("pending_url") or f"https://{r.domain}",
            source="retro-promote",
            priority=40,
        )
        promoted.append(r.domain)

    return {
        "status": "ok",
        "classified": {k: classified.get(k) for k in ("scored", "errors", "unreadable")},
        "promoted": len(promoted),
        "promoted_domains": promoted[:40],
        "threshold": FRONTIER_CONTENT_PROMOTE_SCORE,
    }
