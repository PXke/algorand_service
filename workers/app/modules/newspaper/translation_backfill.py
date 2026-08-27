"""Fast-path backfill for the DeepSeek-routed languages of DEEPSEEK_TRANSLATE_LANGS.

Context (2026-08-26 investigation, see config.py's DEEPSEEK_TRANSLATE_LANGS
and DEEPSEEK_MODEL_TRANSLATE for the full picture): 88 of 115 published
articles predate the routine per-language backfill and are missing most of
their 7 non-Pashto translations. `backfill_article_translations_task`
already exists for exactly this ("queue missing translations for feed-
visible articles") -- it calls `enqueue_missing_article_translations`, which
enqueues ONE `translate_article_batch_task` per article covering EVERY
missing language, always onto the dedicated `translate` queue
(`app.tasks.newspaper.translate_article_batch`'s exact-name entry in
`celery_app.py`'s task_routes). That queue is consumed by a single worker
running `--pool=solo --concurrency=1` (see `deploy/scripts/run_celery.sh`'s
own docstring) -- a restriction that exists ONLY to protect the local
MiLMMT-46-4B-v1.0 model from a fork-vs-free-threading crash triggered by
`torch`/`transformers` initialization (confirmed live 2026-08-17, same class
of bug as the `from_pretrained` ThreadPoolExecutor segfault
`local_translate.py`'s `HF_DEACTIVATE_ASYNC_LOAD` already works around).

A DeepSeek-only translation call never touches that code path at all --
`local_translate.py` imports `torch`/`transformers` lazily, inside its own
model-loading function, never at module import time (confirmed by reading
the file: the only `import torch` / `from transformers import ...`
statements live inside `_load_milmmt`), and `_run_deepseek_translations` /
`_translate_one_lang_via_deepseek` never call into `local_translate.py` at
all. So a `translate_article_batch_task` invocation whose `langs` argument
contains ONLY DeepSeek-routed languages never imports `torch`, and is
exactly as fork-safe under the ordinary prefork pool as any other plain
HTTP-calling Celery task on this platform.

This module's `dispatch_deepseek_translation_backfill` exploits that: it is
the SAME `translate_article_batch_task` Celery task (already correctly
off-peak-gated for its DeepSeek portion -- see that task's own docstring in
`publish_tasks.py`, already Typesense/IndexNow-wired, already idempotent
against a stale scan), just with two differences at the DISPATCH site
(`send_task`'s own `queue=` kwarg wins over the static `task_routes` entry,
per Celery's own routing precedence):

  1. only DeepSeek-routed languages are ever included in a dispatched
     article's `langs` list (so `local_pending` inside the task is always
     empty for these dispatches, and `torch` is never imported);
  2. the queue is explicitly overridden to `"pipeline"` -- the SAME queue
     the ordinary `algorand-platform-celery` worker (`--concurrency=4`, no
     `--pool=solo`) already consumes for the rest of `app.tasks.newspaper.*`
     -- instead of the single-language-at-a-time `translate` queue.

No new Celery task is introduced; no change to `translate_article_batch_task`
itself was needed. A genuinely mixed backfill (an article missing BOTH a
DeepSeek-routed and a local-only language, e.g. after narrowing
DEEPSEEK_TRANSLATE_LANGS back down via env) still gets its local-only
languages via the ordinary `backfill_article_translations_task` /
`enqueue_missing_article_translations` path -- this module only ever touches
the DeepSeek-routed subset, and deliberately leaves the rest alone.

Two functions, same read/act split as `gray_zone_reconciliation.py`:

  - `find_deepseek_translation_gaps` is read-only: which feed-visible
    articles are missing which DeepSeek-routed languages, right now. No
    Cassandra writes, no Celery dispatch. Safe to call as often as wanted.

  - `dispatch_deepseek_translation_backfill` is the one function that acts.
    `dry_run=True` by default (mirrors `dispatch_gray_zone_deep_classify`),
    and a REAL dispatch (`dry_run=False`) additionally checks
    `is_off_peak_now()` itself before firing anything -- not because the
    dispatched tasks would do the wrong thing during peak hours (each one
    self-defers via the same off-peak gate `translate_article_batch_task`
    already applies, see that task's docstring), but because firing up to
    `limit` Celery tasks that immediately no-op into "deferred_peak_hours"
    is pointless churn for a caller who can simply wait and try again. A
    dry-run preview is NEVER blocked by this -- inspecting the batch a real
    run WOULD touch is exactly the tool for deciding when to run it for
    real.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def find_deepseek_translation_gaps(
    limit: int | None = None, *, scan_limit: int = 500
) -> list[dict[str, object]]:
    """Read-only report: feed-visible articles missing one or more DEEPSEEK_TRANSLATE_LANGS languages, right now. Makes NO Cassandra writes and dispatches NOTHING.

    `scan_limit` bounds `list_feed_articles`'s own scan (mirrors
    `backfill_article_translations_task`'s own `limit=500` default -- this
    platform publishes ~7 articles/day, so 500 comfortably covers the entire
    88-of-115 backlog this was built for with room to spare). `limit` only
    trims the RETURNED list after the full scan.

    Sorted by `article_id` purely for a stable, deterministic ordering
    across repeat calls with the same `scan_limit` -- mirrors
    `gray_zone_reconciliation._gray_zone_rows`'s own domain-sort for the
    same reason.
    """
    from app.core.config import DEEPSEEK_TRANSLATE_LANGS
    from app.modules.newspaper.article_store import list_feed_articles

    findings: list[dict[str, object]] = []
    for row in list_feed_articles(limit=scan_limit):
        existing = set((row.translations or {}).keys())
        missing = sorted(lang for lang in DEEPSEEK_TRANSLATE_LANGS if lang not in existing)
        if missing:
            findings.append({"article_id": str(row.article_id), "missing_langs": missing})
    findings.sort(key=lambda item: item["article_id"])
    return findings[:limit] if limit is not None else findings


def dispatch_deepseek_translation_backfill(
    *, limit: int = 20, dry_run: bool = True, scan_limit: int = 500
) -> dict[str, object]:
    """Dispatch up to `limit` articles' missing DeepSeek-routed languages to the real `translate_article_batch_task`, explicitly routed to the `pipeline` queue (the ordinary concurrency=4 worker) instead of the single-language-at-a-time `translate` queue -- see this module's own top-of-file docstring for why that's safe.

    `limit` defaults to 20 -- larger than `dispatch_gray_zone_deep_classify`'s
    5, since each dispatch here is a plain DeepSeek API call (no crawl, no
    page fetch) landing on a pool with real concurrency, not a single-worker
    bottleneck; still bounded, not "dispatch the whole backlog in one call",
    so a caller can watch a real run's effect before scaling up.

    `dry_run` defaults to True: reports which articles WOULD be dispatched
    (and which languages) without calling `send_task`. Always safe to call,
    regardless of peak hours -- see the off-peak note below.

    A REAL dispatch (`dry_run=False`) additionally requires `is_off_peak_now()`
    to be True at the moment this function is called; if it isn't, nothing is
    dispatched and the result reports `"status": "skipped_peak_hours"` plus
    `next_off_peak_at` for the caller to retry after. This is a courtesy
    check, not the enforcement mechanism -- each dispatched
    `translate_article_batch_task` re-checks `is_off_peak_now()` itself
    before making any DeepSeek call (see that task's own docstring), so this
    function's own gate exists purely to avoid firing `limit` Celery tasks
    that would immediately no-op during a peak window.

    Every dispatched article re-verifies its own missing-language set at
    task run time (`translate_article_batch_task` already does this), so a
    stale `scan_limit` snapshot -- another process filling a language in
    between the scan and the dispatch actually running -- only ever wastes
    a cheap re-check, never double-translates or overwrites a fresher
    result.
    """
    from app.celery_app import celery_app as _celery_app
    from app.modules.newspaper.peak_hours import is_off_peak_now, next_off_peak_at

    candidates = find_deepseek_translation_gaps(scan_limit=scan_limit)
    batch = candidates[:limit]

    if not dry_run and not is_off_peak_now():
        next_at = next_off_peak_at()
        logger.info(
            "dispatch_deepseek_translation_backfill: skipping real dispatch, still peak "
            "hours -- next off-peak start at %s",
            next_at.isoformat() if next_at else "unknown",
        )
        return {
            "dry_run": dry_run,
            "status": "skipped_peak_hours",
            "next_off_peak_at": next_at.isoformat() if next_at else None,
            "dispatched": [],
            "dispatched_count": 0,
            "remaining_candidates": len(candidates),
        }

    dispatched: list[dict[str, object]] = []
    for item in batch:
        if not dry_run:
            _celery_app.send_task(
                "app.tasks.newspaper.translate_article_batch",
                args=[item["article_id"], item["missing_langs"]],
                queue="pipeline",
            )
        dispatched.append(item)

    return {
        "dry_run": dry_run,
        "status": "ok",
        "dispatched": dispatched,
        "dispatched_count": len(dispatched),
        "remaining_candidates": max(len(candidates) - len(batch), 0),
    }
