#!/usr/bin/env python3
"""Assign a permanent URL slug to every article that does not have one.

Published articles are slugged FIRST, in published_at order. That ordering is
load-bearing, not cosmetic: `articles_by_id` holds far more drafts and
superseded recomposes than live stories, and the pipeline routinely produces
several rows sharing one title. Walking every row by date would hand the bare
slug to a draft and demote the published article to `-2`. Measured on the
2026-07-28 backup: 32 title collisions across all rows, but only ONE among
published articles.

Idempotent. A row that already has a slug is left alone — slugs are permanent
URLs, so this never reassigns one. Re-running only fills gaps.

    python3 scripts/backfill_article_slugs.py --dry-run   # report, write nothing
    python3 scripts/backfill_article_slugs.py --apply
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import UUID

from algorand_shared.slugs import unique_slug


def _session():  # noqa: ANN202 -- driver Session, imported lazily
    from app.core.cassandra import get_cassandra_session

    return get_cassandra_session()


def _claimed(session) -> set[str]:  # noqa: ANN001
    """Every slug already spoken for, so we never hand out a URL twice."""
    return {r.slug for r in session.execute("SELECT slug FROM articles_by_slug") if r.slug}


def _rows(session) -> tuple[list, set]:  # noqa: ANN001
    """(published rows newest-last, ids of published) — published lead the queue."""
    feed = list(
        session.execute("SELECT bucket, article_id, published_at, title, slug FROM articles_feed")
    )
    feed.sort(key=lambda r: (r.published_at or datetime.min.replace(tzinfo=UTC)))
    return feed, {r.article_id for r in feed}


def main() -> int:  # noqa: C901
    """Backfill slugs, published articles first. Returns a process exit code."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op mode")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    session = _session()
    claimed = _claimed(session)
    feed, published_ids = _rows(session)
    print(f"published rows: {len(feed)}  slugs already claimed: {len(claimed)}")

    assigned: list[tuple[str, str, str]] = []  # (article_id, title, slug)
    skipped = 0

    def take(article_id, title: str) -> str:  # noqa: ANN001
        slug = unique_slug(title or "", fallback=str(article_id), is_taken=lambda s: s in claimed)
        claimed.add(slug)
        return slug

    # 1. Published first — they get the clean URLs.
    for row in feed:
        if row.slug:
            skipped += 1
            continue
        slug = take(row.article_id, row.title)
        assigned.append((str(row.article_id), row.title or "", slug))

    # 2. Everything else (drafts, superseded recomposes) queues behind them.
    others = [
        r
        for r in session.execute("SELECT article_id, title, slug FROM articles_by_id")
        if r.article_id not in published_ids and not r.slug
    ]
    others.sort(key=lambda r: str(r.article_id))  # deterministic across re-runs
    for row in others:
        slug = take(row.article_id, row.title)
        assigned.append((str(row.article_id), row.title or "", slug))

    suffixed = [a for a in assigned if a[2].rsplit("-", 1)[-1].isdigit()]
    print(f"to assign: {len(assigned)}  already had one: {skipped}  suffixed: {len(suffixed)}")
    for _aid, title, slug in assigned[:5]:
        print(f"  {slug[:70]:<72} <- {title[:50]}")
    if len(assigned) > 5:
        print(f"  ... and {len(assigned) - 5} more")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    now = datetime.now(tz=UTC)
    for aid, _title, slug in assigned:
        uid = UUID(aid)
        session.execute("UPDATE articles_by_id SET slug=%s WHERE article_id=%s", (slug, uid))
        session.execute(
            "INSERT INTO articles_by_slug (slug, article_id, claimed_at) VALUES (%s, %s, %s)",
            (slug, uid, now),
        )
    # The feed projection carries its own copy so listing pages can build hrefs
    # without a detail lookup. Its PRIMARY KEY is (bucket, published_at,
    # article_id) with bucket a MONTH partition — an UPDATE naming anything
    # less than the full key upserts a phantom null row, which has bitten this
    # table twice. Every key component comes off the row we just read.
    for row in feed:
        if row.slug:
            continue
        match = next((s for a, _t, s in assigned if a == str(row.article_id)), None)
        if match:
            session.execute(
                "UPDATE articles_feed SET slug=%s WHERE bucket=%s "
                "AND published_at=%s AND article_id=%s",
                (match, row.bucket, row.published_at, row.article_id),
            )
    print(f"\nwrote {len(assigned)} slugs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
