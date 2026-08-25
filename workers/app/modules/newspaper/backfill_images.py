"""One-off backfill: give already-published articles a source image.

Articles that published before source-image resolution have an empty image_url,
so their feed tiles and social cards fall back to a generic logo. This walks the
feed, resolves each imageless story's source artwork (og:image, else brand icon),
and writes it back onto the article's `articles` row.

Run on a host with the app env loaded:
    python -m app.modules.newspaper.backfill_images          # apply
    python -m app.modules.newspaper.backfill_images --dry-run
"""

from __future__ import annotations

import logging
import sys
from uuid import UUID

from app.modules.newspaper.article_store import list_feed_articles, update_article_image
from app.modules.newspaper.source_image import resolve_article_images

logger = logging.getLogger(__name__)


def backfill(*, limit: int = 500, dry_run: bool = False) -> dict:
    """Resolve and write a source image for already-published articles missing one."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    scanned = updated = skipped = failed = 0
    for row in list_feed_articles(limit=limit):
        scanned += 1
        aid = str(row.article_id)
        # Article-table consolidation Phase 5: one consolidated row (was
        # articles_by_id's GET_IMAGE_META) -- already carries body too, so no
        # separate get_article() read is needed for the cited-links fallback
        # below.
        meta = session.execute(ArticlesStmts.GET_FULL_BY_ID, (UUID(aid),)).one()
        if meta is None:
            continue
        if (meta.image_url or "").strip():
            skipped += 1
            continue
        # Body needed for the cited-links fallback (editorial://, mail://
        # sources aren't fetchable, so their image comes from the article's
        # own Sources block).
        from app.modules.newspaper.tasks.publish_tasks import _validated_hero_checked

        og, logo = resolve_article_images(
            source_url=meta.source_url,
            service_id=meta.service_id,
            body=meta.body or "",
            validate=_validated_hero_checked,
        )
        image = og or logo
        if not image:
            failed += 1
            logger.info("  no image  %s", meta.service_id)
            continue
        if dry_run:
            logger.info("  WOULD set %s -> %s", meta.service_id, image)
        else:
            update_article_image(aid, image)
            logger.info("  set       %s -> %s", meta.service_id, image)
        updated += 1
    result = {
        "scanned": scanned,
        "updated": updated,
        "skipped_has_image": skipped,
        "no_image_found": failed,
        "dry_run": dry_run,
    }
    logger.info(result)
    return result


def resync_feed_images(*, limit: int = 500, dry_run: bool = False) -> dict:
    """Re-apply each article's own image_url via update_article_image (idempotent).

    Historically copied the detail row's image_url into the SEPARATE feed
    projection when the two could drift out of sync (articles_by_id vs
    articles_feed, pre article-table consolidation -- a partial feed upsert
    could leave a stale/blank image_url on the feed-visible copy while the
    detail row had the real one). `articles` is now a single consolidated row
    per article, so there is nothing left to drift: this pass degrades to a
    harmless no-op re-write, kept only so `--resync` on an old runbook/cron
    entry keeps working rather than erroring outright.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    synced = 0
    for row in list_feed_articles(limit=limit):
        aid = str(row.article_id)
        meta = session.execute(ArticlesStmts.GET_FULL_BY_ID, (UUID(aid),)).one()
        image = (meta.image_url or "").strip() if meta else ""
        if not image:
            continue
        synced += 1
        logger.info("  %s %s -> %s", "WOULD sync" if dry_run else "synced", aid, image[:60])
        if not dry_run:
            update_article_image(aid, image)
    result = {"synced": synced, "dry_run": dry_run}
    logger.info(result)
    return result


def cleanup_phantoms(*, dry_run: bool = False) -> dict:
    """No-op (article-table consolidation Phase 5): this bug class is now structurally impossible.

    A "phantom" row (null service_id/title) could only exist because
    articles_feed was a SEPARATE projection table, upserted independently
    from articles_by_id -- a partial write there could land a malformed row
    with no matching detail row backing it. `articles` has no second,
    independently-written projection to desync from: a status='published'
    row IS the article the public feed reads, not a separate feed-presence
    pointer to one. Kept as a callable no-op (rather than deleted outright,
    and never wired to a Celery beat -- this has only ever been a manual CLI
    script) so `--cleanup` on an old runbook/cron entry degrades to "did
    nothing" instead of an ImportError/AttributeError.
    """
    result = {
        "deleted_phantoms": 0,
        "dry_run": dry_run,
        "note": "structurally impossible under the consolidated `articles` schema",
    }
    logger.info(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    if "--cleanup" in sys.argv:
        cleanup_phantoms(dry_run=dry)
    elif "--resync" in sys.argv:
        resync_feed_images(dry_run=dry)
    else:
        backfill(dry_run=dry)
