"""Celery tasks that index articles and crawled pages into Typesense."""

from __future__ import annotations

import time
import uuid

from app.celery_app import celery_app
from app.modules.crawler.crawled_page_store import upsert_crawled_page
from app.modules.newspaper.article_store import get_article, list_feed_articles
from app.modules.search.classifier.score import score_page
from app.modules.search.core.indexer import upsert_article_document, upsert_page_document


@celery_app.task(name="app.tasks.search.index_article")
def index_article(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    service_id: str,
    published_at_epoch: int,
) -> dict[str, str]:
    """Celery task: upsert an article's document into the Typesense search index."""
    tags: list[str] | None = None
    detail = get_article(article_id)
    if detail is not None:
        tags = list(detail.tags or [])
    return upsert_article_document(
        article_id=article_id,
        title=title,
        summary=summary,
        body=body,
        service_id=service_id,
        published_at_epoch=published_at_epoch,
        tags=tags,
    )


@celery_app.task(name="app.tasks.search.index_crawled_page")
def index_crawled_page(
    *,
    url: str,
    title: str,
    text: str,
    service_id: str,
    published_at_epoch: int | None = None,
) -> dict[str, str]:
    """Index scraped page text when the classifier marks it in-scope.

    An admin-approved domain bypasses this too, same reasoning as the crawl-
    storage gate in web_crawler.py: score_page scores purely on keyword/domain-
    anchor presence in the text, which a legitimate but chain-silent ecosystem
    partner's page can easily score 0.0 on — an explicit human relevance call
    shouldn't lose to that (root-caused 2026-07-21: dark-coin.com passed the
    storage gate after that fix but still never reached the search index).
    """
    result = score_page(url=url, text=text)
    if not result.in_scope:
        from app.modules.crawler.domain_tracker import domain_from_url, is_admin_approved_domain

        if not is_admin_approved_domain(domain_from_url(url)):
            return {
                "status": "skipped",
                "reason": "classifier_rejected",
                "score": str(result.score),
            }
    epoch = published_at_epoch if published_at_epoch is not None else int(time.time())
    stored = upsert_crawled_page(
        url=url,
        title=title,
        body=text,
        service_id=service_id,
        source="web",
        classifier_score=result.score,
    )
    try:
        page_id = str(uuid.UUID(stored.page_id))
    except ValueError:
        page_id = stored.page_id
    indexed = upsert_page_document(
        page_id=page_id,
        url=url,
        domain=stored.domain,
        title=title,
        description=stored.description,
        body=text,
        keywords=list(stored.keywords),
        service_id=service_id,
        published_at_epoch=epoch,
        classifier_score=result.score,
    )
    indexed["classifier_score"] = str(result.score)
    return indexed


@celery_app.task(name="app.tasks.search.reindex_articles")
def reindex_articles(*, limit: int = 200) -> dict[str, object]:
    """Backfill Typesense from Cassandra feed rows."""
    rows = list_feed_articles(limit=limit)
    indexed = 0
    skipped = 0
    errors = 0
    for row in rows:
        detail = get_article(row.article_id)
        if detail is None:
            skipped += 1
            continue
        outcome = upsert_article_document(
            article_id=detail.article_id,
            title=detail.title,
            summary=detail.summary,
            body=detail.body,
            service_id=detail.service_id,
            published_at_epoch=detail.published_at_epoch,
            tags=list(detail.tags or []),
        )
        status = outcome.get("status", "")
        if status == "indexed":
            indexed += 1
        elif status == "error":
            errors += 1
        else:
            skipped += 1
    return {
        "status": "ok",
        "scanned": len(rows),
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
    }
