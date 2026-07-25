"""Shared Typesense client and articles-collection schema/synonyms setup."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    import typesense

logger = logging.getLogger(__name__)

ARTICLES_COLLECTION = "articles"
PAGES_COLLECTION = "pages"

# Multi-way synonyms for the articles collection. Keep in sync with
# workers/app/modules/search/core/indexer.py.
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


def is_typesense_configured() -> bool:
    """True when a Typesense API key is set in settings."""
    return bool(settings.typesense_api_key.strip())


@lru_cache(maxsize=1)
def get_typesense_client() -> typesense.Client | None:
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
    """Create the given Typesense collection if it doesn't already exist."""
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
    """Create the articles collection and patch in the tokens field and synonyms."""
    if not ensure_collection(ARTICLES_SCHEMA):
        return False
    client = get_typesense_client()
    if client is not None:
        _ensure_tokens_field(client)
        ensure_article_search_synonyms(client)
    return True


def _ensure_tokens_field(client: typesense.Client) -> None:
    """Add the optional tokens field to collections created before tokenization."""
    try:
        client.collections[ARTICLES_COLLECTION].update(
            {"fields": [{"name": "tokens", "type": "string[]", "optional": True}]}
        )
    except Exception:
        # Field already exists or collection was created with the current schema.
        logger.debug("tokens field patch skipped", exc_info=True)


def ensure_article_search_synonyms(client: typesense.Client) -> None:
    """Register acronym/geo synonyms (USA↔US, etc.) on the articles collection."""
    synonyms_api = client.collections[ARTICLES_COLLECTION].synonyms
    for syn_id, synonyms in ARTICLE_SEARCH_SYNONYMS.items():
        try:
            synonyms_api.upsert(syn_id, {"synonyms": synonyms})
        except Exception:
            logger.warning("failed to upsert search synonym %r", syn_id, exc_info=True)


def expanded_search_terms(query: str) -> list[str]:
    """Query plus any synonym cluster members (for feed-scan fallback)."""
    q = query.strip().lower()
    if not q:
        return []
    terms = [q]
    for synonyms in ARTICLE_SEARCH_SYNONYMS.values():
        lowered = [s.lower() for s in synonyms]
        if q in lowered:
            terms.extend(lowered)
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def ensure_pages_collection() -> bool:
    """Create the pages collection if it doesn't already exist."""
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
