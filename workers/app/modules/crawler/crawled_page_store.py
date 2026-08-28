"""Persist crawled pages and derive their keyword/description metadata."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.modules.pipeline.core.diffing import normalize_text

logger = logging.getLogger(__name__)

# A client-side router's inline fallback ("Page Not Found") is characteristically
# tiny compared to any real page on the same app (83 chars observed on Lumi
# Rogue vs. 976-1484 for its real shell/homepage) -- length alone is too broad
# a filter (a real page can legitimately be short), so this only fires on the
# combination of short AND explicitly says not-found.
_SOFT_404_MAX_CHARS = 200
_SOFT_404_MARKERS = ("page not found", "404 not found", "could not be found")


def looks_like_soft_404(body: str) -> bool:
    """Whether `body` reads like a client-side router's not-found fallback rather than real content -- shared by every write path into crawled_pages (service_context.py's aggregate selection, writer_fetch_enqueue.py's queue gate, index_tasks.py's storage gate) so a soft-404 never gets treated as discovered content anywhere in the pipeline."""
    text = body.strip()
    if len(text) > _SOFT_404_MAX_CHARS:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _SOFT_404_MARKERS)


def domain_has_similar_content(domain: str, body: str, *, sample_limit: int = 20) -> bool:
    """Whether `body` (normalized) byte-matches a page already crawled for `domain` recently.

    Root-caused 2026-08-28 (Lumi Rogue): a client-rendered SPA serves the SAME
    shell HTML for any route the JS router doesn't recognize (routing never
    executes for a non-browser fetch), so ~20 URL-variant guesses in one crawl
    burst all produced byte-identical "new" pages -- each stored as if it were
    genuinely distinct content. Checked at storage time (index_tasks.py), not
    just at service_context.py's read-time selection, so the corpus itself
    stops accumulating rows that can never be anything but noise.

    Best-effort: any lookup failure returns False (an infra hiccup here must
    never block a legitimate page from being stored) and the check only
    samples the `sample_limit` most-recently-crawled pages -- a full-domain
    scan isn't warranted for what's ultimately a defensive dedup check, not a
    correctness-critical one.
    """
    if not domain or not (body or "").strip():
        return False
    target = normalize_text(body)
    try:
        from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
        from app.core.statements import CrawledPageStmts

        session = get_cassandra_session()
        listing = session.execute(CrawledPageStmts.LIST_BY_DOMAIN, (domain, sample_limit))
        page_ids = [row.page_id for row in listing]
        if not page_ids:
            return False
        bodies = execute_parallel_with_args(CrawledPageStmts.GET_BODY, [(pid,) for pid in page_ids])
        for ok, result in bodies:
            if not ok:
                continue
            detail = result.one()
            if detail is None:
                continue
            if normalize_text(detail.body or "") == target:
                return True
        return False
    except Exception:
        logger.debug("domain_has_similar_content lookup failed for %s", domain, exc_info=True)
        return False


_STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "from",
    "have",
    "will",
    "your",
    "about",
    "into",
    "their",
    "there",
    "which",
    "when",
    "what",
    "where",
    "were",
    "been",
    "also",
    "more",
    "than",
    "over",
    "under",
    "http",
    "https",
    "www",
}


@dataclass(frozen=True)
class CrawledPageRecord:
    """One stored crawled page and its derived metadata."""

    page_id: str
    url: str
    domain: str
    title: str
    description: str
    body: str
    service_id: str
    source: str
    keywords: tuple[str, ...]
    classifier_score: float
    crawled_at_epoch: int


def normalize_domain(url: str) -> str:
    """Raw lowercased netloc (e.g. "www.lumirogue.com") -- the exact domain key `upsert_crawled_page` stores under, distinct from `domain_tracker.domain_from_url`'s eTLD+1 collapse. Callers checking `domain_has_similar_content` against a not-yet-stored URL must use THIS, not the collapsed form, or the lookup misses everything already stored."""
    try:
        return (urlparse(url).netloc or "").strip().lower()
    except Exception:
        return ""


def _short_description(text: str, *, limit: int = 320) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _keyword_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", (text or "").lower())
    return [tok for tok in tokens if tok not in _STOPWORDS]


def _top_keywords(texts: Iterable[str], *, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for text in texts:
        for token in _keyword_terms(text):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tok for tok, _ in ranked[:limit]]


def build_keywords(*, title: str, body: str, domain: str) -> list[str]:
    """Rank keyword terms drawn from a page's title, body prefix and domain."""
    domain_tokens = [part for part in re.split(r"[.\-_/]+", domain) if len(part) >= 3]
    return _top_keywords([title, body[:3000], " ".join(domain_tokens)], limit=12)


def page_id_for_url(url: str) -> uuid.UUID:
    """Deterministic page_id derived from the url alone. crawled_pages_by_id is keyed by this (not by url), so any caller holding just a url can point- look-up an already-harvested page's cached body — no domain scan needed."""
    page_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return uuid.uuid5(uuid.NAMESPACE_URL, f"crawled-page:{page_id}")


def upsert_crawled_page(
    *,
    url: str,
    title: str,
    body: str,
    service_id: str,
    source: str,
    classifier_score: float,
    crawled_at: datetime | None = None,
) -> CrawledPageRecord:
    """Insert or update a crawled page's cached record, keyed by its url-derived id."""
    from app.core.cassandra import get_cassandra_session
    from app.core.config import CRAWLED_PAGE_BODY_MAX_CHARS
    from app.core.statements import CrawledPageStmts

    now = crawled_at or datetime.now(tz=UTC)
    domain = normalize_domain(url)
    if len(body) > CRAWLED_PAGE_BODY_MAX_CHARS:
        # A real page's readable text never gets remotely this large — almost
        # always non-text content misread as a page (root-caused 2026-08-06: a
        # 23MB body took down the whole task as an uncaught Cassandra
        # InvalidRequest, 16MB over its native-protocol message limit).
        # Truncate rather than skip storage entirely: a genuinely huge
        # legitimate page still gets a usable (if partial) cached copy, and a
        # truncated non-text blob is harmless noise, not a crash.
        logger.warning(
            "crawled page body for %s is %d chars (cap %d) — truncating; "
            "likely non-text content (binary file, oversized asset) rather "
            "than a real page",
            url,
            len(body),
            CRAWLED_PAGE_BODY_MAX_CHARS,
        )
        body = body[:CRAWLED_PAGE_BODY_MAX_CHARS]
    description = _short_description(body)
    keywords = build_keywords(title=title, body=body, domain=domain)
    page_uuid = page_id_for_url(url)

    session = get_cassandra_session()
    session.execute(
        CrawledPageStmts.INSERT_BY_ID,
        (
            page_uuid,
            url,
            domain,
            title,
            description,
            body,
            service_id,
            source,
            keywords,
            float(classifier_score),
            now,
        ),
    )
    session.execute(
        CrawledPageStmts.INSERT_BY_DOMAIN,
        (
            domain,
            now,
            page_uuid,
            url,
            title,
            description,
            service_id,
            source,
            keywords,
        ),
    )
    return CrawledPageRecord(
        page_id=str(page_uuid),
        url=url,
        domain=domain,
        title=title,
        description=description,
        body=body,
        service_id=service_id,
        source=source,
        keywords=tuple(keywords),
        classifier_score=float(classifier_score),
        crawled_at_epoch=int(now.timestamp()),
    )
