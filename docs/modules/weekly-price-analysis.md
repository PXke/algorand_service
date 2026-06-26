# Brick: Weekly digest (market + newspaper)

## Goal

Publish a **weekly** newspaper issue combining:

1. Trailing **7-day** price stats (CoinGecko) for ALGO or a configured asset
2. **Highlights** from articles published on the platform in the last 7 days
3. Optional **Mistral** narrative when `MISTRAL_ENABLED=1`

## Status

`partial` (template + Mistral + feed rollup; beat on Monday 09:00 UTC)

## Standards & RFCs

CoinGecko public API (rate limits, attribution); article body [CommonMark](https://spec.commonmark.org/) (informative). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

## Features

- Celery beat `publish_weekly_price_analysis` / `publish_weekly_digest` (same handler)
- Idempotent per ISO week (`article_id` = UUID5 of week key; `trigger_txid` = `weekly-digest-YYYY-Www`)
- `service_id` default `weekly-digest` (legacy `weekly-price-analysis` excluded from highlights)
- Env: `PRICE_ANALYSIS_ENABLED`, `PRICE_ANALYSIS_ASSET_ID`, `WEEKLY_DIGEST_*`

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `PRICE_ANALYSIS_ENABLED` | `1` | Master switch |
| `PRICE_ANALYSIS_ASSET_ID` | `algorand` | CoinGecko coin id |
| `PRICE_ANALYSIS_SERVICE_ID` | `weekly-digest` | Feed `service_id` |
| `WEEKLY_DIGEST_INCLUDE_FEED` | `1` | Roll up recent articles |
| `WEEKLY_DIGEST_LOOKBACK_DAYS` | `7` | Article window |
| `WEEKLY_DIGEST_MAX_ARTICLES` | `25` | Max highlights listed |
| `WEEKLY_DIGEST_FEED_SCAN_LIMIT` | `200` | Rows read from `articles_feed` |
| `MISTRAL_ENABLED` | `0` | Use Mistral for prose |

## Code map

- `workers/app/modules/newspaper/weekly_digest.py` — context + template
- `workers/app/modules/newspaper/weekly_digest_publish.py` — publish + dedupe
- `workers/app/modules/newspaper/price_analysis.py` — CoinGecko fetch
- `workers/app/modules/newspaper/article_composer.py` — `compose_weekly_digest`
- `workers/app/modules/ai/mistral_compose.py` — Mistral digest prompt
- `workers/app/modules/newspaper/tasks/price_analysis_tasks.py`

## Run manually

**Host** (worker venv + local Cassandra/Redis):

```bash
cd workers
export PRICE_ANALYSIS_ENABLED=1
export MISTRAL_ENABLED=1
export MISTRAL_API_KEY=your-key
export CASSANDRA_HOSTS=127.0.0.1 REDIS_URL=redis://127.0.0.1:6379/0
PYTHONPATH=. celery -A app.celery_app call app.tasks.newspaper.publish_weekly_digest
```

**Docker** (after `make docker-app`):

```bash
docker compose exec worker celery -A app.celery_app call app.tasks.newspaper.publish_weekly_digest
curl -s 'http://localhost:8080/api/v1/news/feed?limit=10' | jq '.items[] | select(.service_id=="weekly-digest")'
```

Re-running the same ISO week is a no-op (`status: skipped`, article already present).

## See locally in Flutter

1. `make docker-app` (or API + worker + beat on host).
2. Run digest task above (or wait for Monday 09:00 UTC beat).
3. Open the web app → **News** — look for `weekly-digest` in the feed; article count in the header updates via `/api/v1/news/stats`.

## Depends on

- `article-store`, `celery-redis-queues`, [ai-mistral-connector.md](ai-mistral-connector.md)

## Future improvements

- Link article ids in digest body for Flutter deep links
- On-chain metrics section
- Charts embedded in article view
