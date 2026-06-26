# Brick: Typesense indexer

## Goal

Index published articles into Typesense for Product 3 search (not for newspaper feed).

## Status

`partial` (live indexing on publish + reindex task + classifier-gated pages)

## Features (should do)

- Collection `articles` with title, summary, body, `service_id`, `published_at`
- Collection `pages` for classifier-approved crawled source text
- Worker `index_article` after successful publish
- Worker `index_crawled_page` after scrape (classifier gate)
- Worker `reindex_articles` backfill from Cassandra feed
- Skip when `TYPESENSE_API_KEY` is empty
- Create collection on first index if missing
- Backend client helper for search queries (articles + pages)

## Manual reindex

```bash
cd workers
celery -A app.celery_app call app.tasks.search.reindex_articles --kwargs='{"limit": 200}'
```

## Good to have

- Idempotent upsert by `article_id`
- Log index failures without failing publish pipeline

## Future improvements

- Nightly full reindex from Cassandra
- Separate collections for crawled web pages (P3 crawl brick)
- Synonyms / typo tolerance config for “Algorand” ecosystem terms
- Index only after `algorand-page-classifier` approves page
- Sharded Typesense cluster ops guide

## Standards & RFCs

[Typesense API](https://typesense.org/docs/api/) collection schema and indexing. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#typesense-indexer--search-api).

## Depends on

- `article-store`, Typesense service reachable from workers

## Code map

- `workers/app/modules/search/core/indexer.py`
- `workers/app/modules/search/core/typesense_config.py`
- `workers/app/modules/search/classifier/score.py`
- `workers/app/modules/search/tasks/index_tasks.py`
- `backend/app/core/typesense_client.py`
