"""Search the platform's own published articles for prior mentions."""

from __future__ import annotations

from typing import Any


def search_platform_mentions(
    *,
    service_id: str,
    primary_domain: str,
    display_name: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Prior articles that mention this service, domain, or name (Cassandra scan v1).

    Typesense search by keyword is phase 2.
    """
    from app.modules.newspaper.article_store import list_feed_articles

    needles = [n for n in (primary_domain, service_id, display_name) if n and len(n) > 2]
    if not needles:
        return {"matches": [], "count": 0}

    hits: list[dict[str, str]] = []
    for article in list_feed_articles(limit=200):
        blob = f"{article.title} {article.summary}".lower()
        for needle in needles:
            if needle.lower() in blob:
                hits.append(
                    {
                        "article_id": article.article_id,
                        "title": article.title[:120],
                        "matched": needle,
                    }
                )
                break
        if len(hits) >= limit:
            break

    return {"matches": hits, "count": len(hits)}
