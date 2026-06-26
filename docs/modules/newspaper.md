# Product 1 — Newspaper (overview)

Newspaper is a **product** made of **bricks**. Each brick has its own doc with:

- **Features (should do)**
- **Good to have**
- **Future improvements**

## Goal

On-chain activity on a registered service → scrape → diff → article in Cassandra → feed in Flutter.

## Brick list

| Brick | Doc |
|-------|-----|
| `conduit-cassandra` | [conduit-cassandra.md](conduit-cassandra.md) |
| `chain-read` | [chain-read.md](chain-read.md) |
| `service-registry` | [service-registry.md](service-registry.md) |
| `chain-tail-watcher` | [chain-tail-watcher.md](chain-tail-watcher.md) |
| `worker-scraper` | [worker-scraper.md](worker-scraper.md) |
| `worker-pipeline` | [worker-pipeline.md](worker-pipeline.md) |
| `article-compose` | [article-compose.md](article-compose.md) |
| `article-store` | [article-store.md](article-store.md) |
| `news-api` | [news-api.md](news-api.md) |
| `frontend-newspaper` | [frontend-newspaper.md](frontend-newspaper.md) |
| `weekly-price-analysis` | [weekly-price-analysis.md](weekly-price-analysis.md) — weekly digest (market + feed) |
| `price-metrics-mistral` | [price-metrics-mistral.md](price-metrics-mistral.md) — CoinGecko samples + Mistral brief |
| `ai-mistral-connector` | [ai-mistral-connector.md](ai-mistral-connector.md) — optional LLM compose |

Shared: `cassandra-schema-migrations`, `cassandra-repository`, `celery-redis-queues`.

## Product-level features (should do)

- End-to-end path from new block to visible feed article for registered services
- No Typesense required for feed (search is P3)
- Do not archive all chain txns for newspaper

## Product-level good to have

- At least one real TestNet service seeded and producing articles before freeze
- Celery beat running on TestNet host

## Publish policy

When to publish, daily caps, and article types (discovery vs update vs weekly digest): **[publish-policy.md](publish-policy.md)**.

## Product-level future improvements

- `redis-feed-cache`, `worker-scraper-playwright` as separate bricks
- DEX / swap volume in metrics bar
- LLM articles with richer editorial policy (Mistral optional)

## Flow

See mermaid in prior revisions or [products-and-bricks.md](../architecture/products-and-bricks.md).
