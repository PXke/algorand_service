"""Article search: Typesense-backed with a feed-scan fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
from app.core.typesense_client import (
    ARTICLES_COLLECTION,
    ensure_articles_collection,
    expanded_search_terms,
    get_typesense_client,
)

if TYPE_CHECKING:
    import typesense

    from app.modules.news.stores.base import StoredArticle
from app.modules.news.services.news_service import NewsService
from app.modules.search.models.schemas import SearchHit, SearchResponse

logger = logging.getLogger(__name__)

_HIGHLIGHT_AFFIX_TOKENS = 12


def _normalize_lang(lang: str | None) -> str | None:
    """A supported non-English locale, or None (meaning: English/default fields only)."""
    if not lang:
        return None
    code = lang.strip().lower()
    return code if code in ARTICLE_TRANSLATION_LANGS else None


class SearchService:
    """Article search: Typesense-backed with a feed-scan fallback."""

    def __init__(self, news_service: NewsService | None = None) -> None:
        """Wire the news service used for the feed-scan fallback path."""
        self._news = news_service or NewsService()

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        service_id: str | None = None,
        lang: str | None = None,
    ) -> SearchResponse:
        """Search articles, preferring Typesense and falling back to a feed scan.

        `lang`, when a supported non-English locale (see
        ARTICLE_TRANSLATION_LANGS), additionally searches that language's
        title_<lang>/summary_<lang>/body_<lang> fields, weighted above the
        English fields so a native-language match ranks first while an
        article not yet translated into that language is still findable via
        English. Anything else (None, "en", or an unsupported code) searches
        the English fields only -- unchanged from before this parameter
        existed.
        """
        q = query.strip()
        if not q:
            return SearchResponse(query=q, engine="none", items=[])
        norm_lang = _normalize_lang(lang)

        client = get_typesense_client()
        if client is not None and ensure_articles_collection():
            try:
                hits = self._search_typesense(
                    client, q, limit=limit, service_id=service_id, lang=norm_lang
                )
                return SearchResponse(query=q, engine="typesense", items=hits)
            except Exception as exc:
                logger.warning("Typesense search failed, using feed scan: %s", exc)

        try:
            return self._search_feed_scan(q, limit=limit, service_id=service_id, lang=norm_lang)
        except Exception:
            logger.exception("Feed scan search failed")
            return SearchResponse(query=q, engine="error", items=[])

    def _search_typesense(
        self,
        client: typesense.Client,
        q: str,
        *,
        limit: int,
        service_id: str | None,
        lang: str | None,
    ) -> list[SearchHit]:
        """PUBLISHED ARTICLES only. The crawled-pages collection is the writer's research corpus — surfacing it to readers mixed raw third-party pages into results, with ids that don't resolve to any article route."""
        hits: list[SearchHit] = []
        query_by, query_by_weights = _query_fields_for_lang(lang)
        params: dict = {
            "q": q,
            "query_by": query_by,
            "query_by_weights": query_by_weights,
            "per_page": max(limit, 10),
            "highlight_fields": _highlight_fields_for_lang(lang),
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
            title_hl, snippet = _parse_highlights(found, lang)
            title = str((lang and doc.get(f"title_{lang}")) or doc.get("title", ""))
            summary = str((lang and doc.get(f"summary_{lang}")) or doc.get("summary", ""))
            hits.append(
                SearchHit(
                    article_id=str(doc.get("id", "")),
                    title=title,
                    summary=summary,
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
        lang: str | None,
    ) -> SearchResponse:
        terms = expanded_search_terms(q)
        items = []
        # Use the raw store so feed-scan can search full bodies (feed DTOs omit
        # body text). This path only runs when Typesense is unavailable.
        for article in self._news._store.list_feed(limit=100):
            if service_id and article.service_id != service_id:
                continue
            # A supported locale searches (and excerpts from) the article's
            # own translated text when one is stored, falling back to
            # English field-by-field otherwise -- same intent as the
            # Typesense path's per-language fields, just applied to this
            # rarely-hit fallback (only used when Typesense itself is down).
            view = _localized_view(article, lang)
            if not _feed_article_matches(view, terms):
                continue
            matched = _first_matching_term(view, terms)
            snippet = _feed_excerpt(
                [("body", view.body), ("summary", view.summary), ("title", view.title)],
                matched or terms[0],
            )
            items.append(
                SearchHit(
                    article_id=article.article_id,
                    title=view.title,
                    summary=view.summary,
                    service_id=article.service_id,
                    published_at_epoch=article.published_at_epoch,
                    snippet=snippet,
                )
            )
        items = items[:limit]
        return SearchResponse(query=q, engine="feed_scan", items=items)


_BASE_QUERY_BY = "title,summary,body,tokens"
_BASE_QUERY_WEIGHTS = "4,2,1,6"


def _query_fields_for_lang(lang: str | None) -> tuple[str, str]:
    """query_by fields + weights for the reader's active locale.

    English (lang=None, i.e. unset/"en"/unsupported) searches only the
    English fields -- byte-for-byte the same params as before this locale
    parameter existed, so the common case doesn't regress. A supported
    non-English locale ALSO searches its title_<lang>/summary_<lang>/
    body_<lang> fields, weighted above the English fallback so a native-
    language match ranks first, while English fields stay in the mix so an
    article not yet translated into that language is still findable.
    """
    if not lang:
        return _BASE_QUERY_BY, _BASE_QUERY_WEIGHTS
    lang_fields = f"title_{lang},summary_{lang},body_{lang}"
    lang_weights = "8,4,2"
    return f"{lang_fields},{_BASE_QUERY_BY}", f"{lang_weights},{_BASE_QUERY_WEIGHTS}"


def _highlight_fields_for_lang(lang: str | None) -> str:
    if not lang:
        return "title,summary,body"
    return f"title_{lang},summary_{lang},body_{lang},title,summary,body"


def _text_match_score(found: dict) -> float:
    raw = found.get("text_match", 0)
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def _parse_highlights(found: dict, lang: str | None = None) -> tuple[str | None, str | None]:
    """Return (title_highlight, body_or_summary_snippet) from a Typesense hit, preferring the locale's own fields when a match landed there."""
    by_field: dict[str, str] = {}
    for hl in found.get("highlights", []):
        if not isinstance(hl, dict):
            continue
        field = hl.get("field")
        text = hl.get("snippet") or hl.get("value")
        if field and text:
            by_field[str(field)] = str(text)
    if lang:
        title_hl = by_field.get(f"title_{lang}") or by_field.get("title")
        snippet = (
            by_field.get(f"body_{lang}")
            or by_field.get(f"summary_{lang}")
            or by_field.get("body")
            or by_field.get("summary")
        )
        return title_hl, snippet
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


class _LocalizedArticleView:
    """A title/summary/body view over a StoredArticle, substituting the article's stored translation for `lang` field-by-field where one exists (falling back to English per-field, not all-or-nothing)."""

    __slots__ = ("body", "summary", "title")

    def __init__(self, title: str, summary: str, body: str) -> None:
        self.title = title
        self.summary = summary
        self.body = body


def _localized_view(article: StoredArticle, lang: str | None) -> _LocalizedArticleView:
    """English view when `lang` is unset, or this article has no stored translation for it; otherwise a per-field localized view.

    The feed-scan fallback only runs when Typesense itself is unreachable,
    but it still needs to search (and excerpt from) the locale's own text
    rather than silently degrading to English mid-outage.
    """
    body = str(getattr(article, "body", "") or "")
    if not lang:
        return _LocalizedArticleView(article.title, article.summary, body)
    translations = getattr(article, "translations", None) or {}
    raw = translations.get(lang)
    if not raw:
        return _LocalizedArticleView(article.title, article.summary, body)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        return _LocalizedArticleView(article.title, article.summary, body)
    title = parsed.get("title") if isinstance(parsed.get("title"), str) else None
    summary = parsed.get("summary") if isinstance(parsed.get("summary"), str) else None
    translated_body = parsed.get("body") if isinstance(parsed.get("body"), str) else None
    return _LocalizedArticleView(
        title or article.title,
        summary or article.summary,
        translated_body or body,
    )


def _feed_article_matches(article: StoredArticle | _LocalizedArticleView, terms: list[str]) -> bool:
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


def _first_matching_term(
    article: StoredArticle | _LocalizedArticleView, terms: list[str]
) -> str | None:
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
