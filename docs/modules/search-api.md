# Brick: Search API

## Goal

Search articles via Typesense when configured; fallback for local dev.

## Status

`done`

## Features (should do)

- `GET /api/v1/search?q=&limit=&service_id=`
- Typesense query on `title,summary,body` when client configured
- Fallback `feed_scan` over recent feed when Typesense unavailable
- Return `engine` field in response for transparency
- `SearchHit` with score when from Typesense

## Good to have

- Empty query returns 400 — **done**
- Default limit 20, max cap 100 — **done**

## Future improvements

- Facets: filter by `service_id`, date range
- Highlight snippets in results
- Search crawled pages collection (separate from articles)
- Did-you-mean / fuzzy config per env
- Rate limit search endpoint

## Standards & RFCs

Typesense search API + [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#typesense-indexer--search-api).

## Depends on

- `typesense-indexer`, `news-api`, `article-store`

## Code map

- `backend/app/modules/search/`
