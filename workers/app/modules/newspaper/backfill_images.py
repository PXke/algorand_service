"""One-off backfill: give already-published articles a source image.

Articles that published before source-image resolution have an empty image_url,
so their feed tiles and social cards fall back to a generic logo. This walks the
feed, resolves each imageless story's source artwork (og:image, else brand icon),
and writes it to both the detail row and the feed projection.

Run on a host with the app env loaded:
    python -m app.modules.newspaper.backfill_images          # apply
    python -m app.modules.newspaper.backfill_images --dry-run
"""

from __future__ import annotations

import logging
import sys
from uuid import UUID

from app.modules.newspaper.article_store import list_feed_articles, update_article_image
from app.modules.newspaper.source_image import resolve_source_images

logger = logging.getLogger(__name__)


def backfill(*, limit: int = 500, dry_run: bool = False) -> dict:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    session = get_cassandra_session()
    scanned = updated = skipped = failed = 0
    for row in list_feed_articles(limit=limit):
        scanned += 1
        aid = str(row.article_id)
        meta = session.execute(
            ArticleStmts.GET_IMAGE_META, (UUID(aid),)
        ).one()
        if meta is None:
            continue
        if (meta.image_url or "").strip():
            skipped += 1
            continue
        og, logo = resolve_source_images(
            source_url=meta.source_url, service_id=meta.service_id
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
    """Copy each article's image_url from the detail row into the feed projection
    (idempotent). Heals rows whose feed image got out of sync with the detail."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    session = get_cassandra_session()
    synced = 0
    for row in list_feed_articles(limit=limit):
        aid = str(row.article_id)
        meta = session.execute(ArticleStmts.GET_IMAGE, (UUID(aid),)).one()
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
    """Delete malformed feed rows (null service_id/title) left by an earlier
    partial upsert, so they stop counting against the feed page size."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import FeedStmts

    session = get_cassandra_session()
    rows = session.execute(FeedStmts.SCAN_ALL)
    deleted = 0
    for r in rows:
        if (r.service_id or "") and (r.title or ""):
            continue
        deleted += 1
        logger.info(
            "  %s phantom bucket=%s aid=%s",
            "WOULD delete" if dry_run else "deleted",
            r.bucket,
            r.article_id,
        )
        if not dry_run:
            session.execute(
                FeedStmts.DELETE,
                (r.bucket, r.published_at, r.article_id),
            )
    result = {"deleted_phantoms": deleted, "dry_run": dry_run}
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
