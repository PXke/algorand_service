# Brick: Worker pipeline (diff)

## Goal

Detect **text changes** between page snapshots before composing an article.

## Status

`partial` (whitespace-normalized diff; v1 features complete)

## Example: manual diff task

```bash
# From workers/ with Redis broker running:
celery -A app.celery_app call app.tasks.pipeline.diff_snapshot \
  --args='["previous line\nsame", "previous line\nchanged"]'
```

Expected: unified diff with `changed` line; whitespace-only edits produce an empty diff.

## Features (should do)

- Unified text diff (`difflib`) with line cap
- Normalize whitespace before diff to reduce noise
- `build_text_diff(previous, current)` used in publish path
- Standalone Celery task `diff_snapshot` for testing
- Skip article body diff block when first snapshot (no previous hash)

## Good to have

- Truncate diff in article to max chars (already capped in compose)

## Future improvements

- HTML-aware diff (retain headings)
- Boilerplate removal (nav/footer) before diff
- Language detection; skip non-content blocks
- Structural hash (simhash) for near-duplicate detection
- Multi-page crawl per service (sitemap follower)

## Standards & RFCs

Internal unified-diff format and size caps. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#worker-pipeline).

## Depends on

- `worker-scraper`

## Code map

- `workers/app/modules/pipeline/core/diffing.py`
- `workers/app/modules/pipeline/tasks/pipeline_tasks.py`
