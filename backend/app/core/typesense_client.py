from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.core.config import settings

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


def is_typesense_configured() -> bool:
    return bool(settings.typesense_api_key.strip())


@lru_cache(maxsize=1)
def get_typesense_client() -> Any | None:
    """Return Typesense client or None when API key is unset."""
    if not is_typesense_configured():
        return None
    try:
        import typesense
    except ImportError:
        return None

    return typesense.Client(
        {
            "nodes": [
                {
                    "host": settings.typesense_host,
                    "port": str(settings.typesense_port),
                    "protocol": settings.typesense_protocol,
                }
            ],
            "api_key": settings.typesense_api_key,
            "connection_timeout_seconds": 5,
        }
    )


def ensure_collection(schema: dict[str, object]) -> bool:
    client = get_typesense_client()
    if client is None:
        return False
    name = str(schema["name"])
    try:
        client.collections[name].retrieve()
        return True
    except Exception:
        logger.debug("typesense collection %r not found; creating", name, exc_info=True)
    try:
        client.collections.create(schema)
        return True
    except Exception:
        logger.warning("failed to create typesense collection %r", name, exc_info=True)
        return False


def ensure_articles_collection() -> bool:
    return ensure_collection(ARTICLES_SCHEMA)


def ensure_pages_collection() -> bool:
    return ensure_collection(PAGES_SCHEMA)


def clear_search_index() -> dict[str, object]:
    """Drop article/page search collections so a pipeline reset starts clean."""
    client = get_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_not_configured"}
    cleared: list[str] = []
    for schema in (ARTICLES_SCHEMA, PAGES_SCHEMA):
        name = str(schema["name"])
        try:
            client.collections[name].delete()
            cleared.append(name)
        except Exception:
            logger.warning("failed to delete typesense collection %r", name, exc_info=True)
    return {"status": "ok", "cleared": cleared}
