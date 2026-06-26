# Brick: Worker scraper

## Goal

Fetch public web pages for registered services when chain activity triggers publish.

## Status

`partial` (HTTP + Playwright hard targets + Discord/Telegram/Reddit)

## Example: ad-hoc scrape

```bash
# From workers/ with Celery worker on scrape queue:
celery -A app.celery_app call app.tasks.scrape.fetch_source \
  --args='["my-service", "https://example.com/docs"]'
```

Returns title, content hash, and a 500-char text preview. Chain-triggered publishes use the same scraper inside `publish_from_chain_event`.

## Features (should do)

- HTTP GET with timeout
- Extract title and plain text (BeautifulSoup)
- Content SHA-256 hash for change detection
- `HttpScraper` implementing shared `BaseScraper` interface
- Celery task `fetch_source` for manual/ad-hoc scrape
- Used inside `publish_from_chain_event`
- **Discord:** `discord://channel/<id>` → [worker-scraper-discord.md](worker-scraper-discord.md)
- **Reddit:** `reddit://r/<subreddit>` → [worker-scraper-reddit.md](worker-scraper-reddit.md)

## Good to have

- User-Agent and Accept-Language headers per service
- Respect `robots.txt` for known hosts
- Max body size cap to avoid huge pages

- **Browser (Playwright):** [worker-scraper-browser.md](worker-scraper-browser.md) — `browser://https://…` and allowlisted SPA hosts

## Future improvements
- GitHub releases / RSS adapters per service type
- Per-domain concurrency and backoff
- Screenshot attachment on article (optional)
- Proxy rotation for rate-limited sites

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | HTTP GET |
| [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309) | robots.txt (good to have) |
| [HTML5](https://html.spec.whatwg.org/) | Parsing |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#worker-scraper).

## Depends on

- `celery-redis-queues`

## Code map

- `workers/app/modules/scraper/core/http_scraper.py`
- `workers/app/modules/scraper/tasks/scrape_tasks.py`
