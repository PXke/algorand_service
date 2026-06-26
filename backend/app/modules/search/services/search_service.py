from __future__ import annotations

import logging

from app.core.typesense_client import (
    ARTICLES_COLLECTION,
    PAGES_COLLECTION,
    ensure_articles_collection,
    ensure_pages_collection,
    get_typesense_client,
)
from app.modules.news.services.news_service import NewsService
from app.modules.search.models.schemas import SearchHit, SearchResponse

logger = logging.getLogger(__name__)


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
                if hits:
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
        hits: list[SearchHit] = []
        filter_by = f"service_id:={service_id}" if service_id else None
        collections = [ARTICLES_COLLECTION]
        if ensure_pages_collection():
            collections.append(PAGES_COLLECTION)

        per_collection = max(limit, 10)
        for collection in collections:
            params: dict = {
                "q": q,
                "query_by": "title,description,body,url,domain,keywords"
                if collection == PAGES_COLLECTION
                else "title,summary,body",
                "per_page": per_collection,
            }
            if filter_by:
                params["filter_by"] = filter_by
            result = client.collections[collection].documents.search(params)
            for found in result.get("hits", []):
                doc = found.get("document", {})
                doc_id = str(doc.get("id", ""))
                if collection == PAGES_COLLECTION:
                    url = doc.get("url")
                    summary = str(doc.get("description", "")).strip() or str(doc.get("body", ""))[:240]
                    if url:
                        summary = f"{url}\n{summary}".strip()
                    hits.append(
                        SearchHit(
                            article_id=doc_id,
                            title=str(doc.get("title", "")),
                            summary=summary,
                            service_id=doc.get("service_id"),
                            published_at_epoch=int(doc["published_at"])
                            if doc.get("published_at") is not None
                            else None,
                            score=_text_match_score(found),
                        )
                    )
                else:
                    hits.append(
                        SearchHit(
                            article_id=doc_id,
                            title=str(doc.get("title", "")),
                            summary=str(doc.get("summary", "")),
                            service_id=doc.get("service_id"),
                            published_at_epoch=int(doc["published_at"])
                            if doc.get("published_at") is not None
                            else None,
                            score=_text_match_score(found),
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
        lowered = q.lower()
        items = [
            SearchHit(
                article_id=article.article_id,
                title=article.title,
                summary=article.summary,
                service_id=article.service_id,
                published_at_epoch=article.published_at_epoch,
            )
            for article in self._news.list_feed(limit=100)
            if _feed_article_matches(article, lowered)
        ][:limit]
        if service_id:
            items = [item for item in items if item.service_id == service_id]
        return SearchResponse(query=q, engine="feed_scan", items=items)


def _text_match_score(found: dict) -> float:
    raw = found.get("text_match", 0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def _feed_article_matches(article, lowered_query: str) -> bool:
    haystacks = [article.title.lower(), article.summary.lower()]
    body = getattr(article, "body", None)
    if body:
        haystacks.append(str(body).lower())
    return any(lowered_query in text for text in haystacks)
