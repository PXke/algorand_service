# Brick: Article store

## Goal

Persist articles, snapshots, and optional service events in **Cassandra** (newspaper SoT).

## Status

`done`

## Features (should do)

- Tables: `articles_feed`, `articles_by_id`, `page_sources`, `page_snapshots`, `service_events`
- Worker: insert snapshot on change; insert article + feed row on publish
- Worker: record `service_events` on match (including unchanged hash path)
- API: `CassandraArticleStore` and `InMemoryArticleStore` behind `NEWS_STORE`
- Feed bucket `main` configurable

## Good to have

- Dedupe articles: same hash within 24h → skip second publish
- Store scrape failure reason on event row

## Future improvements

- **Brick `redis-feed-cache`**: hot feed pages
- Archival to cold storage after N days
- Compaction job for old snapshots (keep last K)
- Full-text search in Cassandra (not recommended; use Typesense brick)
- Article amendments / corrections table

## Standards & RFCs

Cassandra CQL time-series clustering; [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) for API JSON. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#article-store--news-api).

## Depends on

- `cassandra-schema-migrations` (app `005`–`007`)
- `article-compose`

## Code map

- `backend/app/modules/news/stores/`
- `workers/app/modules/newspaper/article_store.py`
- `workers/app/modules/newspaper/snapshot_store.py`
