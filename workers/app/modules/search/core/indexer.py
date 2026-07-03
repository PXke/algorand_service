from __future__ import annotations

import logging
from typing import Any

from app.modules.search.core.typesense_config import build_typesense_client, is_typesense_configured

logger = logging.getLogger(__name__)

ARTICLES_COLLECTION = "articles"
PAGES_COLLECTION = "pages"

ARTICLES_SCHEMA = {
    "name": ARTICLES_COLLECTION,
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "summary", "type": "string"},
        {"name": "body", "type": "string"},
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


def _ensure_collection(client: Any, schema: dict[str, object]) -> None:
    name = str(schema["name"])
    try:
        client.collections[name].retrieve()
        return
    except Exception:
        logger.debug("typesense collection %r not found; creating", name, exc_info=True)
    client.collections.create(schema)


def upsert_article_document(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    service_id: str,
    published_at_epoch: int,
) -> dict[str, str]:
    if not is_typesense_configured():
        return {"status": "skipped", "reason": "typesense_not_configured"}

    client = build_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_client_unavailable"}

    try:
        _ensure_collection(client, ARTICLES_SCHEMA)
        client.collections[ARTICLES_COLLECTION].documents.upsert(
            {
                "id": article_id,
                "title": title,
                "summary": summary,
                "body": body,
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
