"""Upsert articles/pages into the Typesense search index."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from algorand_shared.glossary_refs import extract_glossary_slugs

from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
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

# Typesense's default tokenizer splits on Unicode word boundaries, which is
# whitespace-driven -- fine for every ARTICLE_TRANSLATION_LANGS script except
# Chinese, which has no spaces between words at all. Only zh needs an
# explicit per-field locale hint for correct segmentation; fa/ps/ar
# (Arabic-script but space-delimited between words), ru (Cyrillic), hi
# (Devanagari) and es/fr (Latin) all tokenize correctly with the default.
_CJK_LOCALE_HINTS = {"zh"}


def _translation_field_defs() -> list[dict[str, object]]:
    """Per-language title_<lang>/summary_<lang>/body_<lang> fields, all optional.

    Keeping every one of these optional means adding them to an existing
    live collection (via the `update` patch below, same pattern as the
    `tokens` field) never requires a document migration -- documents indexed
    before this change simply lack the fields, and Typesense treats an
    absent optional field as "does not match on it", not an error.
    """
    fields: list[dict[str, object]] = []
    for lang in ARTICLE_TRANSLATION_LANGS:
        locale = lang if lang in _CJK_LOCALE_HINTS else None
        for base in ("title", "summary", "body"):
            field: dict[str, object] = {
                "name": f"{base}_{lang}",
                "type": "string",
                "optional": True,
            }
            if locale:
                field["locale"] = locale
            fields.append(field)
    return fields


ARTICLES_SCHEMA = {
    "name": ARTICLES_COLLECTION,
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "summary", "type": "string"},
        {"name": "body", "type": "string"},
        {"name": "tokens", "type": "string[]", "optional": True},
        {"name": "service_id", "type": "string", "facet": True},
        {"name": "published_at", "type": "int64", "sort": True},
        # Slugs of glossary terms this article links (English body + every
        # translated body, unioned -- see algorand_shared.glossary_refs).
        # Facet+optional so a glossary term page can filter
        # `glossary_slugs:=slug` to list referencing articles, and older
        # documents indexed before this field existed just don't match it.
        {"name": "glossary_slugs", "type": "string[]", "facet": True, "optional": True},
        *_translation_field_defs(),
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
        _ensure_translation_fields(client)
        _ensure_glossary_slugs_field(client)
        _ensure_article_search_synonyms(client)


def _ensure_translation_fields(client: typesense.Client) -> None:
    """Patch the per-language translation fields into a collection created before this change existed (same pattern as the tokens-field patch)."""
    try:
        client.collections[ARTICLES_COLLECTION].update({"fields": _translation_field_defs()})
    except Exception:
        # Fields already exist, or the collection was just created with the
        # current schema (which already includes them).
        logger.debug("translation field patch skipped", exc_info=True)


def _ensure_glossary_slugs_field(client: typesense.Client) -> None:
    """Add the optional glossary_slugs field to collections created before it existed (same patch pattern as tokens/translation fields)."""
    try:
        client.collections[ARTICLES_COLLECTION].update(
            {
                "fields": [
                    {"name": "glossary_slugs", "type": "string[]", "facet": True, "optional": True}
                ]
            }
        )
    except Exception:
        logger.debug("glossary_slugs field patch skipped", exc_info=True)


def _translation_document_fields(translations: dict[str, str] | None) -> dict[str, str]:
    """Flatten a translations map (lang -> JSON string of {title, summary, body}) into Typesense's title_<lang>/summary_<lang>/body_<lang> document fields.

    Best-effort: an unsupported language key or malformed/non-dict JSON is
    skipped rather than raised, since a stored translations map can outlive
    a language being added/removed from ARTICLE_TRANSLATION_LANGS.
    """
    if not translations:
        return {}
    fields: dict[str, str] = {}
    for lang, raw in translations.items():
        if lang not in ARTICLE_TRANSLATION_LANGS:
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("skipping malformed translation JSON for lang=%s", lang)
            continue
        if not isinstance(parsed, dict):
            continue
        for base in ("title", "summary", "body"):
            value = parsed.get(base)
            if isinstance(value, str) and value:
                fields[f"{base}_{lang}"] = value
    return fields


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
    translations: dict[str, str] | None = None,
) -> dict[str, str]:
    """Upsert an article's searchable fields into the Typesense articles collection.

    `translations` (the article row's lang -> JSON-string map, same shape as
    `ArticleDetail.translations`) is flattened into the per-language
    title_<lang>/summary_<lang>/body_<lang> fields when present, so a full
    reindex (initial publish, admin edit, or the backfill script) carries
    forward whatever's already translated at that moment. A translation that
    lands LATER goes through `upsert_article_translation` instead, which
    merges into the existing document without needing the English fields
    again.
    """
    if not is_typesense_configured():
        return {"status": "skipped", "reason": "typesense_not_configured"}

    client = build_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_client_unavailable"}

    try:
        _ensure_collection(client, ARTICLES_SCHEMA)
        tokens = build_article_search_tokens(title=title, summary=summary, body=body, tags=tags)
        translation_fields = _translation_document_fields(translations)
        document = {
            "id": article_id,
            "title": title,
            "summary": summary,
            "body": body,
            "tokens": tokens,
            "service_id": service_id,
            "published_at": published_at_epoch,
            "glossary_slugs": extract_glossary_slugs(
                body, *(v for k, v in translation_fields.items() if k.startswith("body_"))
            ),
        }
        document.update(translation_fields)
        client.collections[ARTICLES_COLLECTION].documents.upsert(document)
        return {"status": "indexed", "collection": ARTICLES_COLLECTION, "id": article_id}
    except Exception as exc:
        logger.warning("typesense_article_index_failed id=%s error=%s", article_id, exc)
        return {"status": "error", "detail": str(exc), "id": article_id}


def upsert_article_translation(
    *,
    article_id: str,
    lang: str,
    title: str,
    summary: str,
    body: str,
) -> dict[str, str]:
    """Merge one freshly-landed language's fields into an already-indexed article's Typesense document.

    This is the hook `translate_article_batch_task._persist` calls the
    moment a translation is stored -- before this function existed, a
    translation only ever reached Cassandra and IndexNow; Typesense (site
    search) never learned about it, which is the root cause of French (and
    every other locale) search only ever returning English results.

    Uses a partial update (merge), not a full upsert, so it doesn't need to
    already know the article's English title/summary/body. If the document
    doesn't exist yet -- a translation can in principle land before the
    publish-time `index_article` task has run -- falls back to a full
    reindex built from the current article row (which by this point already
    has the just-persisted translation stored, via `update_article_translations`
    called immediately before this).
    """
    if lang not in ARTICLE_TRANSLATION_LANGS:
        return {"status": "skipped", "reason": "unsupported_lang"}
    if not is_typesense_configured():
        return {"status": "skipped", "reason": "typesense_not_configured"}

    client = build_typesense_client()
    if client is None:
        return {"status": "skipped", "reason": "typesense_client_unavailable"}

    try:
        _ensure_collection(client, ARTICLES_SCHEMA)
        update_fields: dict[str, object] = {
            f"title_{lang}": title,
            f"summary_{lang}": summary,
            f"body_{lang}": body,
        }
        # A Typesense .update() replaces string[] fields wholesale, not
        # append -- so a newly-landed translation's glossary links must be
        # unioned with whatever the document already carries (from the
        # English body, and any other language translated earlier) rather
        # than overwriting it down to just this one language's slugs.
        new_slugs = extract_glossary_slugs(body)
        if new_slugs:
            try:
                existing = client.collections[ARTICLES_COLLECTION].documents[article_id].retrieve()
                existing_slugs = set(existing.get("glossary_slugs") or [])
            except Exception:
                existing_slugs = set()
            update_fields["glossary_slugs"] = sorted(existing_slugs | set(new_slugs))
        client.collections[ARTICLES_COLLECTION].documents[article_id].update(update_fields)
        return {"status": "indexed", "collection": ARTICLES_COLLECTION, "id": article_id}
    except Exception as exc:
        logger.warning(
            "typesense_article_translation_update_failed id=%s lang=%s error=%s -- "
            "falling back to a full reindex",
            article_id,
            lang,
            exc,
        )
        return _fallback_full_reindex(article_id)


def _fallback_full_reindex(article_id: str) -> dict[str, str]:
    """Rebuild and upsert an article's whole Typesense document from its current Cassandra row.

    Used when a partial translation update fails because the document was
    never indexed in the first place (rather than a transient error) --
    without this, that translation would be silently dropped instead of
    just landing a beat later than usual.
    """
    from app.modules.newspaper.article_store import get_article

    detail = get_article(article_id)
    if detail is None:
        return {"status": "error", "detail": "article_not_found", "id": article_id}
    return upsert_article_document(
        article_id=detail.article_id,
        title=detail.title,
        summary=detail.summary,
        body=detail.body,
        service_id=detail.service_id,
        published_at_epoch=detail.published_at_epoch,
        tags=list(detail.tags or []),
        translations=detail.translations,
    )


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
