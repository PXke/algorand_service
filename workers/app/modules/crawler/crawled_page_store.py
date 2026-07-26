"""Persist crawled pages and derive their keyword/description metadata."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

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


def _normalize_domain(url: str) -> str:
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
    from app.core.statements import CrawledPageStmts

    now = crawled_at or datetime.now(tz=UTC)
    domain = _normalize_domain(url)
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
