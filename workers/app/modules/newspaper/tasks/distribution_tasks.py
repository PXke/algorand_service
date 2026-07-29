"""Fires social distribution (Bluesky, Telegram, ...) after an article goes live — called from every path that publishes genuinely NEW content (fresh auto-publish, the daily queue-drain release, and admin-approved publishes triggered from the backend). Deliberately NOT called from recompose auto-apply — reposting every refresh of already-published content would look repetitive to followers. Always uses the generated share-card image (/og/article/{id}.png), not whatever "real" image_url the article happens to have — guarantees a consistent branded card on every channel instead of sometimes falling back to a source's low-res favicon."""

from __future__ import annotations

import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.newspaper.distribute_article")
def distribute_article(article_id: str) -> dict[str, object]:
    """Post a newly-published article's share card to every configured distribution channel."""
    from app.core import config
    from app.modules.distribution.base import ArticleShare
    from app.modules.distribution.dispatcher import distribute
    from app.modules.newspaper.article_store import get_article
    from app.modules.newspaper.indexnow import article_url

    article = get_article(article_id)
    if article is None:
        return {"status": "not_found", "article_id": article_id}

    share = ArticleShare(
        title=article.title,
        summary=article.summary,
        url=article_url(article_id, slug=getattr(article, "slug", None)),
        image_url=f"{config.PUBLIC_SITE_URL}/og/article/{article_id}.png",
        tags=tuple(article.tags),
    )
    try:
        results = distribute(share)
    except Exception as exc:
        logger.warning("distribution dispatcher failed for %s: %s", article_id, exc, exc_info=True)
        return {"status": "error", "article_id": article_id, "detail": str(exc)}

    return {
        "status": "ok",
        "article_id": article_id,
        "channels": {r.channel: r.ok for r in results},
    }
