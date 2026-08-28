"""Aggregated service-watch context (Phase 2 of the service-watch redesign).

The unit we snapshot, diff, and compose from is the SERVICE, not one page: a
~12k-token aggregate of the service's recently harvested pages across all of
its domains (service_sources), with the freshly scraped entry page first. The
weekly diff of this aggregate is the product-evolution story — a new forum
post, a rebrand, a docs change all surface as added lines, with the full
surrounding context available to the composer.

Stability matters more than completeness: sections are ordered by URL (never
by recency) and trimmed to fixed sizes, so two aggregates built a week apart
differ only where the SERVICE's content differs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from app.modules.crawler.crawled_page_store import looks_like_soft_404
from app.modules.pipeline.core.diffing import normalize_text


@dataclass(frozen=True)
class ContextPage:
    """One page's content aggregated into a service-watch snapshot."""

    url: str
    title: str
    body: str


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def _hosts_for_service(service_id: str, entry_url: str) -> list[str]:
    """Hosts whose harvested pages belong to this service's aggregate: each web-source domain, its www. twin, the exact hosts of the source URLs, and the entry URL's host. (Harvest partitions key on the raw netloc, so a registrable domain needs its host variants enumerated.)."""
    from app.modules.newspaper.service_sources import list_sources

    hosts: set[str] = set()
    for source in list_sources(service_id):
        if not source.enabled or source.source_type != "web":
            continue
        if source.domain:
            hosts.add(source.domain)
            hosts.add(f"www.{source.domain}")
        netloc = (urlparse(source.url).netloc or "").lower()
        if netloc:
            hosts.add(netloc)
    entry_host = (urlparse(entry_url).netloc or "").lower()
    if entry_host:
        hosts.add(entry_host)
        hosts.add(entry_host.removeprefix("www."))
    return sorted(h for h in hosts if h)


def _recent_harvested_pages(
    hosts: list[str], *, exclude_url: str, max_pages: int, max_age_days: int
) -> list[ContextPage]:
    from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
    from app.core.statements import CrawledPageStmts

    get_cassandra_session()
    cutoff = datetime.now(tz=UTC) - timedelta(days=max_age_days)
    excluded = _norm_url(exclude_url)

    candidates: dict[str, tuple[datetime, object, str, str]] = {}
    listings = execute_parallel_with_args(
        CrawledPageStmts.LIST_BY_DOMAIN, [(host, 40) for host in hosts]
    )
    for ok, result in listings:
        if not ok:
            continue
        for row in result:
            crawled = row.crawled_at
            if crawled is not None and crawled.tzinfo is None:
                crawled = crawled.replace(tzinfo=UTC)
            if crawled is None or crawled < cutoff:
                continue
            key = _norm_url(row.url)
            if not key or key == excluded:
                continue
            known = candidates.get(key)
            if known is None or crawled > known[0]:
                candidates[key] = (crawled, row.page_id, row.url, row.title or "")

    # SELECTION is a per-host round-robin of newest-first pages: a busy host
    # (e.g. a blog being refreshed daily) must not crowd the service's other
    # hosts (forum, docs, second domain) out of the aggregate — multi-host
    # representation is the point of a merged service.
    #
    # Root-caused 2026-08-28 (Lumi Rogue): a client-rendered SPA served the
    # SAME shell HTML (JS routing never executes for a non-browser fetch)
    # for ~20 crawler-guessed URL variants (?view=gungi, /play/gungi,
    # /#/gungi, ...) inside one crawl burst -- because the OLD cap here
    # applied to `picked` (fair-share order, still one slot per URL) BEFORE
    # dedup, that burst filled the entire max_pages budget with near-
    # duplicate/soft-404 content and crowded out every genuinely different
    # older page. Fair-share now runs over the FULL candidate pool (already
    # bounded -- LIST_BY_DOMAIN caps at 40 rows/host), bodies are fetched for
    # all of it, and both filters below run BEFORE the max_pages cut so
    # duplicates/soft-404s never occupy a slot a real page could have used.
    ordered = _fair_share_by_host(list(candidates.values()), max_pages=len(candidates))
    bodies = execute_parallel_with_args(
        CrawledPageStmts.GET_BODY, [(page_id,) for _, page_id, _, _ in ordered]
    )
    return _select_distinct_pages(ordered, bodies, max_pages=max_pages)


def _select_distinct_pages(
    ordered: list[tuple[datetime, object, str, str]],
    bodies: list[tuple[bool, object]],
    *,
    max_pages: int,
) -> list[ContextPage]:
    """Walk `ordered` (fair-share priority order) pairing each candidate with its fetched body, skipping soft-404s and content-duplicates of an already-accepted page, until max_pages distinct real pages are collected."""
    pages: list[ContextPage] = []
    seen_content: set[str] = set()
    for (_, _, url, title), (ok, result) in zip(ordered, bodies, strict=True):
        if len(pages) >= max_pages:
            break
        detail = result.one() if ok else None
        body = (detail.body if detail else "") or ""
        if not body.strip() or looks_like_soft_404(body):
            continue
        content_key = normalize_text(body)
        if content_key in seen_content:
            continue
        seen_content.add(content_key)
        pages.append(ContextPage(url=url, title=title or detail.title or "", body=body))
    return pages


def _fair_share_by_host(
    candidates: list[tuple[datetime, object, str, str]], *, max_pages: int
) -> list[tuple[datetime, object, str, str]]:
    """Round-robin newest-first across hosts (hosts iterated in name order for determinism): every host lands its freshest pages before any host lands its second-freshest."""
    by_host: dict[str, list[tuple[datetime, object, str, str]]] = {}
    for cand in candidates:
        host = (urlparse(cand[2]).netloc or "").lower()
        by_host.setdefault(host, []).append(cand)
    for host in by_host:
        by_host[host].sort(key=lambda c: c[0], reverse=True)
    picked: list[tuple[datetime, object, str, str]] = []
    hosts = sorted(by_host)
    while len(picked) < max_pages and any(by_host[h] for h in hosts):
        for host in hosts:
            if by_host[host] and len(picked) < max_pages:
                picked.append(by_host[host].pop(0))
    return picked


def _section(page: ContextPage, *, per_page_chars: int) -> str:
    # Preserve line breaks (only collapse horizontal whitespace) — the diff
    # this feeds is line-based (pipeline.core.diffing), so flattening a
    # multi-paragraph page to one line makes it diff as "everything removed"
    # against any historical multi-line snapshot (the Algopay false-diff
    # incident, 2026-07-06).
    body = normalize_text(page.body or "")[:per_page_chars]
    title = " ".join((page.title or "").split())[:200]
    return f"## PAGE: {page.url}\n### {title}\n{body}\n"


def build_service_context(
    *,
    service_id: str,
    display_name: str,
    entry_url: str,
    entry_title: str,
    entry_text: str,
    pages: list[ContextPage] | None = None,
) -> str:
    """The service's aggregate text: entry page first, then harvested pages in URL order, capped at SERVICE_CONTEXT_MAX_CHARS. Falls back to the entry page alone when the service has no harvest yet (new domain, first poll) — behaviourally identical to the old single-page watch. ``pages`` is injectable for tests."""
    from app.core.config import (
        SERVICE_CONTEXT_MAX_AGE_DAYS,
        SERVICE_CONTEXT_MAX_CHARS,
        SERVICE_CONTEXT_MAX_PAGES,
        SERVICE_CONTEXT_PER_PAGE_CHARS,
    )

    if pages is None:
        try:
            hosts = _hosts_for_service(service_id, entry_url)
            pages = _recent_harvested_pages(
                hosts,
                exclude_url=entry_url,
                max_pages=SERVICE_CONTEXT_MAX_PAGES,
                max_age_days=SERVICE_CONTEXT_MAX_AGE_DAYS,
            )
        except Exception:
            pages = []

    parts = [f"# SERVICE WATCH: {display_name or service_id}\n"]
    parts.append(
        _section(
            ContextPage(url=entry_url, title=entry_title, body=entry_text),
            per_page_chars=SERVICE_CONTEXT_PER_PAGE_CHARS,
        )
    )
    # URL order for LAYOUT (selection was by recency): the aggregate — and so
    # its snapshot hash — must be stable across polls when content is unchanged.
    parts.extend(
        _section(page, per_page_chars=SERVICE_CONTEXT_PER_PAGE_CHARS)
        for page in sorted(pages, key=lambda p: _norm_url(p.url))
    )
    out: list[str] = []
    used = 0
    for part in parts:
        if used + len(part) > SERVICE_CONTEXT_MAX_CHARS:
            break
        out.append(part)
        used += len(part)
    return "\n".join(out)


def refresh_service_pages(service_id: str, *, entry_url: str, limit: int = 8) -> int:
    """Re-queue the aggregate's page URLs for a fresh crawl so next week's aggregate reflects current content (the frontier only revisits a URL when something links to it again — a watched service shouldn't depend on that). Per-URL cooldown and domain budgets still apply inside enqueue_url."""
    from app.core.config import (
        SERVICE_CONTEXT_MAX_AGE_DAYS,
        SERVICE_CONTEXT_MAX_PAGES,
    )
    from app.modules.crawler.url_queue import enqueue_url
    from app.modules.newspaper.service_sources import list_sources

    urls: list[str] = []
    # The service's OTHER web sources (merged domains/subdomains — the forum,
    # algorand.com, docs.): the beat only scrapes the registry entry URL, so
    # these must be seeded into the crawl queue or their hosts never get
    # harvested and never enter the aggregate.
    with contextlib.suppress(Exception):
        urls.extend(
            source.url
            for source in list_sources(service_id)
            if (
                source.enabled
                and source.source_type == "web"
                and source.url
                and _norm_url(source.url) != _norm_url(entry_url)
            )
        )
    try:
        hosts = _hosts_for_service(service_id, entry_url)
        pages = _recent_harvested_pages(
            hosts,
            exclude_url=entry_url,
            max_pages=min(limit, SERVICE_CONTEXT_MAX_PAGES),
            max_age_days=SERVICE_CONTEXT_MAX_AGE_DAYS,
        )
        urls.extend(page.url for page in pages)
    except Exception:
        pass
    queued = 0
    for url in urls:
        _, created = enqueue_url(
            url,
            source="service_watch_refresh",
            priority=15,
            metadata={"service_id": service_id},
        )
        if created:
            queued += 1
    return queued
