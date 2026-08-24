"""Batch-recompose articles that have never been recomposed since first publish
(first_published_at IS NULL -- see article_store.py's replace_article_content,
which only ever sets it on the FIRST content update).

Fires app.tasks.newspaper.recompose_published ONE AT A TIME, waiting for each
to reach a terminal state before enqueueing the next. This is deliberate, not
just polite pacing: recompose_published's own ComposeBusyError handler
(publish_tasks.py, "the worker runs concurrency=4, so batched recomposes DO
collide") is *supposed* to retry with a 180s backoff when the global
compose_lock is held by a sibling task -- but a live validation run
(2026-08-23) found that retry not actually recovering; the task just ends in
FAILURE instead. Serializing submissions from this script avoids ever
triggering that collision in the first place, sidestepping the bug rather
than fixing Celery's retry plumbing here.

Each recompose is an archive refresh: re-scrapes the source, composes a
fresh draft, and either auto-applies onto the live article (URL/id
unchanged, published_at re-stamped) or holds for review, per
RECOMPOSE_AUTO_APPLY's bar.

Usage: python3 batch_recompose_never_touched.py [--limit N] [--ids-file path]
"""

import argparse
import json
import time

from celery.result import AsyncResult

from app.celery_app import celery_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N")
    parser.add_argument(
        "--ids-file", default="/home/guillaume/never_recomposed.json", help="Path to the JSON list"
    )
    parser.add_argument(
        "--results-file",
        default="/home/guillaume/batch_recompose_results.json",
        help="Where to write per-article outcomes",
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=15.0, help="Poll interval while waiting"
    )
    parser.add_argument(
        "--stuck-timeout-seconds",
        type=float,
        default=1800.0,
        help=(
            "Give up waiting on one task after this long and move on. "
            "recompose_published has no acks_late (unlike translate_article_batch_task) -- "
            "a worker restart mid-compose silently loses the task with no SUCCESS/FAILURE "
            "ever reported, which hung this exact script for 15+ minutes during a live "
            "deploy on 2026-08-23. This bounds that failure mode instead of fixing it."
        ),
    )
    args = parser.parse_args()

    with open(args.ids_file) as f:
        articles = json.load(f)

    if args.limit:
        articles = articles[: args.limit]

    print(f"processing {len(articles)} articles, one recompose at a time", flush=True)
    results = []
    for i, a in enumerate(articles, start=1):
        r = celery_app.send_task("app.tasks.newspaper.recompose_published", args=[a["article_id"]])
        print(f"[{i}/{len(articles)}] {a['article_id']} | {a['title'][:70]} -> task {r.id}", flush=True)

        waited = 0.0
        stuck = False
        while True:
            time.sleep(args.poll_seconds)
            waited += args.poll_seconds
            ar = AsyncResult(r.id, app=celery_app)
            if ar.state in ("SUCCESS", "FAILURE"):
                break
            if waited >= args.stuck_timeout_seconds:
                stuck = True
                print(
                    f"    !! stuck for {waited:.0f}s (state={ar.state}) -- "
                    "giving up and moving on",
                    flush=True,
                )
                break

        outcome = {
            "article_id": a["article_id"],
            "title": a["title"],
            "task_id": r.id,
            "state": "STUCK_TIMEOUT" if stuck else ar.state,
            "result": str(ar.result),
        }
        results.append(outcome)
        print(f"    -> {ar.state}: {ar.result}", flush=True)

        with open(args.results_file, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["state"] == "SUCCESS")
    failed = sum(1 for r in results if r["state"] == "FAILURE")
    stuck_count = sum(1 for r in results if r["state"] == "STUCK_TIMEOUT")
    print(
        f"\nBATCH_DONE ok={ok} failed={failed} stuck_timeout={stuck_count} total={len(results)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
