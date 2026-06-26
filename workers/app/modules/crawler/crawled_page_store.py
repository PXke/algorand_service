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
    domain_tokens = [part for part in re.split(r"[.\-_/]+", domain) if len(part) >= 3]
    return _top_keywords([title, body[:3000], " ".join(domain_tokens)], limit=12)


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
    from app.core.cassandra import get_cassandra_session

    now = crawled_at or datetime.now(tz=UTC)
    domain = _normalize_domain(url)
    page_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    description = _short_description(body)
    keywords = build_keywords(title=title, body=body, domain=domain)
    page_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"crawled-page:{page_id}")

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO crawled_pages_by_id (
          page_id, url, domain, title, description, body, service_id, source,
          keywords, classifier_score, crawled_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
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
        """
        INSERT INTO crawled_pages_by_domain (
          domain, crawled_at, page_id, url, title, description, service_id, source, keywords
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
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


def crawled_page_count_for_url(url: str) -> int:
    """Pages already harvested for the URL's domain (single-partition COUNT).
    Used to front-load a new domain's initial harvest at high priority."""
    from app.core.cassandra import get_cassandra_session

    domain = _normalize_domain(url)
    if not domain:
        return 0
    try:
        session = get_cassandra_session()
        row = session.execute(
            "SELECT COUNT(*) AS c FROM crawled_pages_by_domain WHERE domain = %s",
            (domain,),
        ).one()
        return int(row.c) if row and row.c is not None else 0
    except Exception:
        return 0

