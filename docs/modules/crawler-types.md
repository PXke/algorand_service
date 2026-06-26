# Crawler types (differentiated lanes)

Each crawler is a **separate lane** with its own driver, DB row in `crawler_config`, and env override.

## News / ingest crawlers

| `crawler_type` | Driver | Default enabled | What it does |
|----------------|--------|-----------------|--------------|
| `web` | `WebCrawlerDriver` | **yes** | `https://` HTTP; optional SPA via `CRAWLER_WEB_SPA_ENABLED` |
| `reddit` | `RedditCrawlerDriver` | **no** | `reddit://r/…` JSON/OAuth poll |
| `telegram` | `TelegramCrawlerDriver` | **no** | `telegram://` / `t.me/s/` |
| `discord` | `DiscordCrawlerDriver` | **no** | `discord://` bot or web (SPA required for web) |
| `mail` | `MailCrawlerDriver` | **yes** | IMAP inbox poll |
| `chain` | `ChainCrawlerDriver` | **yes** | Conduit tx match → optional `scrape_url` publish |

**Not crawlers:** push API, Firefox extension (mirror ingest).

## Metrics crawler (charts)

| `crawler_type` | Driver | Default | What it does |
|----------------|--------|---------|--------------|
| `metrics` | `MetricsCrawlerDriver` | **yes** | CoinGecko price today; TVL/nodes planned |

Editorial caps (7/day) do **not** apply to metrics samples.

## Configuration

1. **Database** — `crawler_config` (migration `013`):

```cql
SELECT crawler_type, enabled FROM crawler_config;
```

2. **Environment** (overrides DB when set):

```bash
CRAWLER_WEB_SPA_ENABLED=0      # Playwright inside web crawler
CRAWLER_REDDIT_ENABLED=0
CRAWLER_TELEGRAM_ENABLED=0
CRAWLER_DISCORD_ENABLED=0
CRAWLER_MAIL_ENABLED=1
CRAWLER_CHAIN_ENABLED=1
CRAWLER_METRICS_ENABLED=1
```

Legacy: `CRAWLER_HTTP_ENABLED` → `web`; `CRAWLER_BROWSER_ENABLED` → web SPA.

## Code map

- `workers/app/modules/scraper/crawler_types.py`
- `workers/app/modules/scraper/crawler_registry.py`
- `workers/app/modules/scraper/crawler_dispatch.py`
- `workers/app/modules/scraper/crawlers/*.py`
- `workers/app/celery_app.py` — beat schedules per enabled type

## Scam alerts + enrichment

Foundation Discord warnings (e.g. algoblow.com) should use **push/extension**, then [scam-article-enrichment.md](scam-article-enrichment.md) before Mistral writes the article.
