#!/usr/bin/env python3
"""Backfill a service_id match-key for every published article missing one.

Root-caused 2026-08-04 (UNDP Blockchain Academy, 3rd near-duplicate article
in 3 days): prior_service_article_summary() -- the mechanism that warns the
writer "we already covered this service" before it composes -- depends
entirely on article_match_keys having a service_id row for that service's
most recent article. The July UNDP piece had ZERO match_keys rows at all
(neither service_id nor anything else), so the writer had no prior-coverage
context and confidently re-introduced a story we'd already told twice. The
mechanism itself works -- a same-day editorial-brief article DID get a key
-- the gap is specifically in articles that never had register_article_match_keys
called for them (published before that call site existed, or any other gap
in the create-only registration path).

Idempotent -- skips any article that already has a service_id key, so
re-running only fills genuine gaps. Registers ONLY the service_id key (not
the fuller domain/address/topic-gated set build_match_keys would produce for
a live compose) -- that's the one key prior_service_article_summary's
find_latest_service_article() actually looks up, and it's the one every
article already has on its own row regardless of age.

    python3 scripts/backfill_service_match_keys.py --dry-run
    python3 scripts/backfill_service_match_keys.py --apply
"""

from __future__ import annotations

import argparse


def _session():  # noqa: ANN202 -- driver Session, imported lazily
    from app.core.cassandra import get_cassandra_session

    return get_cassandra_session()


def _feed_rows(session) -> list:  # noqa: ANN001
    """Every published article's (article_id, service_id, published_at) -- full scan, same convention as backfill_article_slugs.py."""
    return list(
        session.execute("SELECT article_id, service_id, published_at FROM articles_feed")
    )


def _has_service_key(session, article_id) -> bool:  # noqa: ANN001
    rows = session.execute(
        "SELECT key_type FROM article_match_keys_by_article WHERE article_id = %s",
        (article_id,),
    )
    return any(r.key_type == "service_id" for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op mode")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    session = _session()
    rows = _feed_rows(session)
    print(f"published rows scanned: {len(rows)}")

    from app.modules.newspaper.article_matching import (
        edit_window_closes_at,
        register_article_match_keys,
    )

    missing: list[tuple[str, str, object]] = []  # (article_id, service_id, published_at)
    skipped_has_key = 0
    skipped_no_service_id = 0
    for row in rows:
        service_id = (row.service_id or "").strip()
        if not service_id:
            skipped_no_service_id += 1
            continue
        if _has_service_key(session, row.article_id):
            skipped_has_key += 1
            continue
        missing.append((str(row.article_id), service_id, row.published_at))

    print(
        f"missing service_id key: {len(missing)}  "
        f"already have one: {skipped_has_key}  no service_id: {skipped_no_service_id}"
    )

    if not apply:
        for article_id, service_id, _published_at in missing[:20]:
            print(f"  would register: article={article_id} service_id={service_id}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        print("Dry run -- pass --apply to write.")
        return 0

    registered = 0
    for article_id, service_id, published_at in missing:
        # Backdate the edit window to when it would ACTUALLY have closed had
        # this key been registered at real publish time -- every one of
        # these articles is old enough that this is already in the past, so
        # the backfill can never make a stale article look freshly editable
        # to find_article_for_followup (register_article_match_keys defaults
        # to now+24h, which would do exactly that for up to a day post-backfill).
        closes_at = edit_window_closes_at(from_time=published_at)
        register_article_match_keys(
            article_id=article_id, keys=[("service_id", service_id)], closes_at=closes_at
        )
        registered += 1
    print(f"registered {registered} service_id match keys")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
