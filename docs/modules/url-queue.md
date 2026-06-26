# URL queue

Persistent Cassandra queue for URLs discovered by chain tail, social crawlers, seeds, and push ingest.

## Schema (migration 016)

- `url_queue` — primary row per item (`queue_id`, `url`, `source`, `priority`, `status`, `metadata`)
- `url_queue_by_url` — deduplication by normalized URL
- `url_queue_pending` — dequeue ordering by `(status, priority DESC, enqueued_at ASC)`

## API (workers)

- `enqueue_url(url, source=..., priority=...)` → `(queue_id, created)`
- `dequeue_url()` → item dict or `None`
- `mark_url_done(queue_id)`

## Drain

Celery beat task `app.tasks.crawler.drain_url_queue` runs every `URL_QUEUE_DRAIN_SECONDS` (default 60s) and hands items to `WebCrawlerDriver.scrape_from_queue_item`.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `URL_QUEUE_ENABLED` | `1` | Master switch |
| `URL_QUEUE_DRAIN_SECONDS` | `60` | Beat interval |

Push ingest with an `url` field enqueues here instead of requiring inline `page_text`.
