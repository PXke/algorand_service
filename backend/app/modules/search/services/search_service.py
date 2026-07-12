from __future__ import annotations

import logging
import re

from app.core.typesense_client import (
    ARTICLES_COLLECTION,
    ensure_articles_collection,
    expanded_search_terms,
    get_typesense_client,
)
from app.modules.news.services.news_service import NewsService
from app.modules.search.models.schemas import SearchHit, SearchResponse

logger = logging.getLogger(__name__)

_HIGHLIGHT_AFFIX_TOKENS = 12


class SearchService:
    def __init__(self, news_service: NewsService | None = None) -> None:
        self._news = news_service or NewsService()

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        service_id: str | None = None,
    ) -> SearchResponse:
        q = query.strip()
        if not q:
            return SearchResponse(query=q, engine="none", items=[])

        client = get_typesense_client()
        if client is not None and ensure_articles_collection():
            try:
                hits = self._search_typesense(client, q, limit=limit, service_id=service_id)
                return SearchResponse(query=q, engine="typesense", items=hits)
            except Exception as exc:
                logger.warning("Typesense search failed, using feed scan: %s", exc)

        try:
            return self._search_feed_scan(q, limit=limit, service_id=service_id)
        except Exception:
            logger.exception("Feed scan search failed")
            return SearchResponse(query=q, engine="error", items=[])

    def _search_typesense(
        self,
        client,
        q: str,
        *,
        limit: int,
        service_id: str | None,
    ) -> list[SearchHit]:
        """PUBLISHED ARTICLES only. The crawled-pages collection is the writer's
        research corpus — surfacing it to readers mixed raw third-party pages
        into results, with ids that don't resolve to any article route."""
        hits: list[SearchHit] = []
        params: dict = {
            "q": q,
            "query_by": "title,summary,body,tokens",
            "query_by_weights": "4,2,1,6",
            "per_page": max(limit, 10),
            "highlight_fields": "title,summary,body",
            "highlight_affix_num_tokens": _HIGHLIGHT_AFFIX_TOKENS,
            "prioritize_exact_match": True,
            "prefix": _typesense_prefix_enabled(q),
            "num_typos": _typesense_num_typos(q),
            "enable_synonyms": True,
        }
        if service_id:
            params["filter_by"] = f"service_id:={service_id}"
        result = client.collections[ARTICLES_COLLECTION].documents.search(params)
        for found in result.get("hits", []):
            doc = found.get("document", {})
            title_hl, snippet = _parse_highlights(found)
            hits.append(
                SearchHit(
                    article_id=str(doc.get("id", "")),
                    title=str(doc.get("title", "")),
                    summary=str(doc.get("summary", "")),
                    service_id=doc.get("service_id"),
                    published_at_epoch=int(doc["published_at"])
                    if doc.get("published_at") is not None
                    else None,
                    score=_text_match_score(found),
                    snippet=snippet,
                    title_highlight=title_hl,
                )
            )

        hits.sort(key=lambda item: item.score or 0.0, reverse=True)
        return hits[:limit]

    def _search_feed_scan(
        self,
        q: str,
        *,
        limit: int,
        service_id: str | None,
    ) -> SearchResponse:
        terms = expanded_search_terms(q)
        items = []
        # Use the raw store so feed-scan can search full bodies (feed DTOs omit
        # body text). This path only runs when Typesense is unavailable.
        for article in self._news._store.list_feed(limit=100):
            if service_id and article.service_id != service_id:
                continue
            if not _feed_article_matches(article, terms):
                continue
            matched = _first_matching_term(article, terms)
            snippet = _feed_excerpt(
                [
                    ("body", str(getattr(article, "body", "") or "")),
                    ("summary", article.summary),
                    ("title", article.title),
                ],
                matched or terms[0],
            )
            items.append(
                SearchHit(
                    article_id=article.article_id,
                    title=article.title,
                    summary=article.summary,
                    service_id=article.service_id,
                    published_at_epoch=article.published_at_epoch,
                    snippet=snippet,
                )
            )
        items = items[:limit]
        return SearchResponse(query=q, engine="feed_scan", items=items)


def _text_match_score(found: dict) -> float:
    raw = found.get("text_match", 0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def _parse_highlights(found: dict) -> tuple[str | None, str | None]:
    """Return (title_highlight, body_or_summary_snippet) from a Typesense hit."""
    by_field: dict[str, str] = {}
    for hl in found.get("highlights", []):
        if not isinstance(hl, dict):
            continue
        field = hl.get("field")
        text = hl.get("snippet") or hl.get("value")
        if field and text:
            by_field[str(field)] = str(text)
    title_hl = by_field.get("title")
    snippet = by_field.get("body") or by_field.get("summary")
    return title_hl, snippet


def _feed_excerpt(fields: list[tuple[str, str]], lowered_query: str) -> str | None:
    """Plain-text excerpt around the first feed-scan match (no Typesense)."""
    for _, text in fields:
        if not text:
            continue
        lower = text.lower()
        idx = lower.find(lowered_query)
        if idx < 0:
            continue
        start = max(0, idx - 80)
        end = min(len(text), idx + len(lowered_query) + 80)
        excerpt = text[start:end].strip()
        if start > 0:
            excerpt = f"…{excerpt}"
        if end < len(text):
            excerpt = f"{excerpt}…"
        return excerpt
    return None


def _typesense_prefix_enabled(query: str) -> bool:
    # "usa" prefix-matches "usability"; short tokens need whole-word matching.
    return len(query.strip()) >= 5


def _typesense_num_typos(query: str) -> int:
    # Skip fuzzy matching on short acronyms (USA, UK, EU).
    return 0 if len(query.strip()) <= 4 else 2


def _word_boundary_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.lower())
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE | re.UNICODE)


def _feed_article_matches(article, terms: list[str]) -> bool:
    haystacks = [article.title, article.summary]
    body = getattr(article, "body", None)
    if body:
        haystacks.append(str(body))
    for text in haystacks:
        if not text:
            continue
        for term in terms:
            if _word_boundary_pattern(term).search(text):
                return True
    return False


def _first_matching_term(article, terms: list[str]) -> str | None:
    haystacks = [
        ("body", str(getattr(article, "body", "") or "")),
        ("summary", article.summary),
        ("title", article.title),
    ]
    for _, text in haystacks:
        if not text:
            continue
        for term in terms:
            if _word_boundary_pattern(term).search(text):
                return term
    return None
