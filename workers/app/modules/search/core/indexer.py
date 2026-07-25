"""Upsert articles/pages into the Typesense search index."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.modules.search.core.tokenize import build_article_search_tokens
from app.modules.search.core.typesense_config import build_typesense_client, is_typesense_configured

if TYPE_CHECKING:
    import typesense

logger = logging.getLogger(__name__)

ARTICLES_COLLECTION = "articles"
PAGES_COLLECTION = "pages"

# Keep in sync with backend/app/core/typesense_client.py
ARTICLE_SEARCH_SYNONYMS: dict[str, list[str]] = {
    "geo-usa": ["usa", "us", "u.s.", "u.s.a.", "united states"],
    "geo-uk": ["uk", "u.k.", "united kingdom"],
    "geo-eu": ["eu", "e.u.", "european union"],
}

ARTICLES_SCHEMA = {
    "name": ARTICLES_COLLECTION,
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "summary", "type": "string"},
        {"name": "body", "type": "string"},
        {"name": "tokens", "type": "string[]", "optional": True},
        {"name": "service_id", "type": "string", "facet": True},
        {"name": "published_at", "type": "int64", "sort": True},
    ],
    "default_sorting_field": "published_at",
}

PAGES_SCHEMA = {
    "name": PAGES_COLLECTION,
    "fields": [
        {"name": "url", "type": "string"},
        {"name": "domain", "type": "string", "facet": True},
        {"name": "title", "type": "string"},
        {"name": "description", "type": "string"},
        {"name": "body", "type": "string"},
        {"name": "keywords", "type": "string[]", "facet": True, "optional": True},
        {"name": "service_id", "type": "string", "facet": True},
        {"name": "classifier_score", "type": "float", "optional": True},
        {"name": "published_at", "type": "int64", "sort": True},
    ],
    "default_sorting_field": "published_at",
}


def _ensure_collection(client: typesense.Client, schema: dict[str, object]) -> None:
    name = str(schema["name"])
    try:
        client.collections[name].retrieve()
    except Exception:
        logger.debug("typesense collection %r not found; creating", name, exc_info=True)
        client.collections.create(schema)
    if name == ARTICLES_COLLECTION:
        _ensure_article_search_synonyms(client)


def _ensure_article_search_synonyms(client: typesense.Client) -> None:
    synonyms_api = client.collections[ARTICLES_COLLECTION].synonyms
    for syn_id, synonyms in ARTICLE_SEARCH_SYNONYMS.items():
        try:
            synonyms_api.upsert(syn_id, {"synonyms": synonyms})
        except Exception:
            logger.warning("failed to upsert search synonym %r", syn_id, exc_info=True)


def upsert_article_document(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    service_id: str,
    published_at_epoch: int,
    tags: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str]:
    """Upsert an article's searchable fields into the Typesense articles collection."""
    if not is_typesense_configured():
        return {"status": "skipped", "reason": "typesense_not_configured"}

    client = build_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_client_unavailable"}

    try:
        _ensure_collection(client, ARTICLES_SCHEMA)
        tokens = build_article_search_tokens(title=title, summary=summary, body=body, tags=tags)
        client.collections[ARTICLES_COLLECTION].documents.upsert(
            {
                "id": article_id,
                "title": title,
                "summary": summary,
                "body": body,
                "tokens": tokens,
                "service_id": service_id,
                "published_at": published_at_epoch,
            }
        )
        return {"status": "indexed", "collection": ARTICLES_COLLECTION, "id": article_id}
    except Exception as exc:
        logger.warning("typesense_article_index_failed id=%s error=%s", article_id, exc)
        return {"status": "error", "detail": str(exc), "id": article_id}


def upsert_page_document(
    *,
    page_id: str,
    url: str,
    domain: str,
    title: str,
    description: str,
    body: str,
    keywords: list[str] | tuple[str, ...] | None,
    service_id: str,
    published_at_epoch: int,
    classifier_score: float,
) -> dict[str, str]:
    """Upsert a crawled page's searchable fields into the Typesense pages collection."""
    if not is_typesense_configured():
        return {"status": "skipped", "reason": "typesense_not_configured"}

    client = build_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_client_unavailable"}

    try:
        _ensure_collection(client, PAGES_SCHEMA)
        client.collections[PAGES_COLLECTION].documents.upsert(
            {
                "id": page_id,
                "url": url,
                "domain": domain,
                "title": title,
                "description": description,
                "body": body[:8000],
                "keywords": list(keywords or []),
                "service_id": service_id,
                "classifier_score": classifier_score,
                "published_at": published_at_epoch,
            }
        )
        return {"status": "indexed", "collection": PAGES_COLLECTION, "id": page_id}
    except Exception as exc:
        logger.warning("typesense_page_index_failed url=%s error=%s", url, exc)
        return {"status": "error", "detail": str(exc), "id": page_id}
