# Brick: Worker scraper — Reddit

## Goal

Crawl **two (or more) Reddit communities** (subreddits) registered in `service_registry` and feed the newspaper pipeline.

## Status

`partial` (public JSON listings + poll)

## Features (should do)

- `scrape_url` scheme `reddit://r/<subreddit>` or `reddit://r/<subreddit>/<sort>`
- Sorts: `hot` (default), `new`, `top`, `rising`, `controversial`
- `RedditScraper` via `https://www.reddit.com/r/{sub}/hot.json`
- **`REDDIT_USER_AGENT` required** ([Reddit API rules](https://github.com/reddit-archive/reddit/wiki/API))
- Celery beat `poll_reddit_sources` every `REDDIT_POLL_SECONDS` (default 600s)
- Seed template: `deploy/seeds/reddit_services.toml` (two communities)

## Good to have

- OAuth app for higher rate limits — **implemented** (`REDDIT_OAUTH_ENABLED`, `reddit_oauth.py`)
- Comments thread depth
- Filter by flair / keyword

## Future improvements

- Official Reddit API v2 with client credentials
- Cross-post deduplication across subreddits

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | HTTP GET |
| [Reddit API wiki](https://github.com/reddit-archive/reddit/wiki/API) | User-Agent policy |

## Depends on

- `worker-scraper`, `service-registry`, `celery-redis-queues`, `article-compose`

## Code map

- `workers/app/modules/scraper/core/reddit_scraper.py`
- `workers/app/modules/scraper/core/reddit_urls.py`
- `workers/app/modules/scraper/tasks/reddit_poll_tasks.py`
- `deploy/seeds/reddit_services.toml`

## Setup

1. Set a descriptive user agent, e.g.  
   `REDDIT_USER_AGENT=algorand-platform-newspaper/1.0 (by u/yourname)`
2. Edit `deploy/seeds/reddit_services.toml` — replace `REPLACE_SUBREDDIT_1` and `REPLACE_SUBREDDIT_2`.
3. Seed: `SEED_FILE=deploy/seeds/reddit_services.toml python3 deploy/scripts/seed_service_registry.py`
4. Run Celery **worker + beat** with the same env.

Manual poll:

```bash
cd workers && PYTHONPATH=. celery -A app.celery_app call app.tasks.scrape.poll_reddit_sources
```
