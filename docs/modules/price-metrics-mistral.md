# Brick: Price metrics for Mistral articles

## Goal

Periodically poll CoinGecko, store price samples in Cassandra, and rebuild a **Mistral-ready context brief** so weekly digest and price articles use grounded, time-windowed facts.

## Status

`done`

## Standards & RFCs

CoinGecko `simple/price` API; internal Cassandra CQL ([cql-migrations.md](../architecture/cql-migrations.md) app `008`). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

## Flow

1. Celery beat `collect_price_metrics` (default every **3600s**).
2. `fetch_spot_tick` — CoinGecko `simple/price` (spot, 24h change, cap, volume).
3. `INSERT` into `price_metric_samples`.
4. Load last 7 days of samples; optional `fetch_weekly_price` chart for reference band.
5. `build_brief` → `INSERT` into `price_metrics_brief` (`mistral_context` text).
6. `mistral_compose` loads `load_mistral_context(asset_id)` into price/digest prompts.

## Schema

Migration `app/008`: `price_metric_samples`, `price_metrics_brief`.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `PRICE_METRICS_ENABLED` | `1` | Master switch |
| `PRICE_METRICS_ASSET_ID` | `PRICE_ANALYSIS_ASSET_ID` | CoinGecko id |
| `PRICE_METRICS_POLL_SECONDS` | `3600` | Beat interval |
| `PRICE_METRICS_SAMPLE_LIMIT` | `200` | Max rows read per prepare |
| `PRICE_METRICS_BRIEF_MAX_CHARS` | `4000` | Cap stored context |

## Code map

- `workers/app/modules/metrics/price_metrics_*.py`
- `workers/app/modules/metrics/tasks/price_metrics_tasks.py`
- `workers/app/modules/ai/mistral_compose.py` — injects brief

## Manual run

**Host:**

```bash
cd workers
export PRICE_METRICS_ENABLED=1
export CASSANDRA_HOSTS=127.0.0.1 REDIS_URL=redis://127.0.0.1:6379/0
PYTHONPATH=. celery -A app.celery_app call app.tasks.metrics.collect_price_metrics
```

**Docker:**

```bash
make docker-app
docker compose exec worker celery -A app.celery_app call app.tasks.metrics.collect_price_metrics
```

Inspect brief in Cassandra (after migration `app/008`):

```bash
docker compose exec cassandra cqlsh -e \
  "SELECT asset_id, prepared_at, length(mistral_context) FROM algorand_platform.price_metrics_brief;"
```

## API (Flutter header)

`GET /api/v1/metrics/price?asset_id=algorand` reads `price_metrics_brief` and returns spot price + 24h change for the newspaper ticker (`backend/app/modules/metrics/`).

## Depends on

- Cassandra app migration 008, `celery-redis-queues`, [ai-mistral-connector.md](ai-mistral-connector.md)

## Future improvements

- Retention compaction / TTL on samples
- Multi-asset support
