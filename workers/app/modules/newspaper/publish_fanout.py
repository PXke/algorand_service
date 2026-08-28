"""Shared "an article just went live" fanout, and the compose-cadence bookkeeping that goes with it.

Before this module existed, `_finalize_publish` (direct auto-publish),
`_release_pending_feed_backlog` (paced backlog release), and
`apply_recomposed_article` (recompose swap-onto-live) each reimplemented the
same "publish this article" tail -- search-index it, enqueue its
translations, ping IndexNow, maybe queue social distribution -- with
divergent error handling between the copies. Most visibly,
`_release_pending_feed_backlog` never called `index_article` at all, so an
article released from the backlog silently never entered Typesense search
until the once-daily `reindex_articles` safety net caught it (W4-A,
2026-08-28). `fanout_after_publish` is now the single place that logic lives;
every "an article is now live" call site should call it instead of
reimplementing any part of it.

`record_compose_cadence` is a separate, smaller duplication: the
`record_domain_compose` / `record_service_compose` / `mark_brief_run` block
that runs once per COMPOSE_MAX_PER_DOMAIN_PER_DAY-consuming compose, whether
that compose landed straight on the feed (`_finalize_publish`) or was stored
held/backlogged for later (`_hold_for_review`). It is deliberately NOT part
of `fanout_after_publish`: a backlog release or a recompose swap doesn't
consume a fresh compose slot (the original compose already recorded one), so
calling this from `fanout_after_publish` would double-count.
"""

from __future__ import annotations

import contextlib
import logging
import time

from app.celery_app import celery_app
from app.modules.crawler.domain_tracker import record_domain_compose, record_service_compose
from app.modules.newspaper.article_store import ensure_article_slug, get_article
from app.modules.newspaper.editorial_assignment import mark_brief_run
from app.modules.newspaper.indexnow import ping_article
from app.modules.newspaper.tasks.distribution_tasks import distribute_article
from app.modules.search.tasks.index_tasks import index_article, index_crawled_page

logger = logging.getLogger(__name__)


def fanout_after_publish(
    article_id: str,
    *,
    distribute: bool,
    page_text: str = "",
    page_title: str = "",
) -> dict[str, object]:
    """Everything that happens once `article_id` is live on the public feed.

    Re-reads the article's current title/summary/body/service_id/
    published_at/slug from storage rather than taking them as parameters --
    every call site publishes (inserts or updates the row) immediately
    before calling this, so the row is always fresh, and a single source of
    truth here is what stops the three copies this replaces from silently
    drifting on which fields they passed.

    Each step is independent and best-effort: a failure in one (a Celery
    broker hiccup on the search-index enqueue, an IndexNow timeout, a
    distribution-dispatcher error) is logged and never blocks the others,
    and never propagates out of this function -- the article is already
    durably published by the time this runs, so raising here would not
    protect anything and would just risk the caller's task retrying (or
    failing) an already-successful publish.

    distribute=False for a recompose swap (see apply_recomposed_article):
    reposting every refresh of already-published content to social channels
    would look repetitive to followers.

    page_text/page_title feed `index_crawled_page` for a fresh crawl's own
    page text (only `_finalize_publish`, the direct-publish path, has
    this) -- omitted (the default), the crawled-page index step is skipped,
    matching every other call site's existing behavior.
    """
    article = get_article(article_id)
    if article is None:
        logger.warning(
            "fanout_after_publish: article %s not found -- nothing to fan out", article_id
        )
        return {"status": "error", "reason": "article_not_found", "article_id": article_id}

    published_at_epoch = article.published_at_epoch or int(time.time())

    try:
        index_article.delay(
            article_id=article_id,
            title=article.title,
            summary=article.summary,
            body=article.body,
            service_id=article.service_id,
            published_at_epoch=published_at_epoch,
        )
    except Exception:
        logger.warning(
            "fanout_after_publish: failed to queue search index for %s", article_id, exc_info=True
        )

    if page_text:
        try:
            index_crawled_page.delay(
                url=article.source_url,
                title=page_title,
                text=page_text,
                service_id=article.service_id,
            )
        except Exception:
            logger.warning(
                "fanout_after_publish: failed to queue crawled-page index for %s",
                article_id,
                exc_info=True,
            )

    # enqueue_article_translations already catches and logs its own
    # failures (see enqueue_missing_article_translations) -- no try/except
    # needed here, but imported locally (not at module top) to avoid a
    # circular import: publish_tasks.py imports this module.
    from app.modules.newspaper.tasks.publish_tasks import enqueue_article_translations

    enqueue_article_translations(article_id)

    # Notify IndexNow (Bing/Ecosia/DuckDuckGo, Yandex, Seznam, Naver) so the
    # new/updated story gets crawled in minutes. Best-effort -- never let it
    # block a publish.
    try:
        ping_article(article_id, slug=ensure_article_slug(article_id, article.title))
    except Exception:
        logger.warning("IndexNow ping failed for article %s", article_id, exc_info=True)

    if distribute:
        # Auto-post to social channels (Bluesky, Telegram, ...) -- best-
        # effort, never blocks the publish itself.
        try:
            distribute_article.delay(article_id=article_id)
        except Exception:
            logger.warning("failed to queue distribution for article %s", article_id, exc_info=True)

    return {"status": "ok", "article_id": article_id}


@celery_app.task(name="app.tasks.newspaper.fanout_after_publish")
def fanout_after_publish_task(article_id: str, *, distribute: bool = True) -> dict[str, object]:
    """Celery entry point for `fanout_after_publish`.

    Backend and workers are separate deployables that don't share a Python
    process -- backend's admin classifier-review approve path (an article
    going live outside the compose pipeline entirely) reaches this fanout
    via `send_task("app.tasks.newspaper.fanout_after_publish", ...)` rather
    than reimplementing it (see backend's `_publish_or_queue_article`).
    Workers callers that are already inside this process call
    `fanout_after_publish` directly instead -- no reason to round-trip
    through the broker for a same-process call.
    """
    return fanout_after_publish(article_id, distribute=distribute)


def record_compose_cadence(
    *,
    compose_domain: str,
    service_id: str,
    article_id: str = "",
    is_editorial_assignment: bool = False,
    brief_id: str = "",
) -> None:
    """Stamp the per-domain/per-service compose cooldown and, for an editorial-assignment source, mark its brief as run.

    Called exactly once per compose that consumes a
    COMPOSE_MAX_PER_DOMAIN_PER_DAY slot -- whether the draft landed straight
    on the feed (`_finalize_publish`) or was stored held/backlogged for a
    human or the pacer to release later (`_hold_for_review`). A later
    backlog release or recompose swap must NOT call this again: the compose
    slot was already recorded when the draft was first produced.
    """
    if compose_domain:
        record_domain_compose(compose_domain)
    if service_id:
        record_service_compose(service_id)
    if is_editorial_assignment:
        with contextlib.suppress(Exception):
            mark_brief_run(brief_id=brief_id, article_id=article_id)
