# Brick: News API

## Goal

Expose newspaper feed and article detail from the article store.

## Status

`done`

## Features (should do)

- `GET /api/v1/news/feed?limit=&service_id=`
- `GET /api/v1/news/articles/{article_id}`
- `GET /api/v1/news/stats` — article count in feed bucket
- `NEWS_STORE=memory|cassandra` for dev vs TestNet
- Public read (no auth required for feed in v1)

## Good to have

- `ETag` / `Last-Modified` for feed caching — **done** (304 on `If-None-Match`, `Cache-Control: max-age=30`)
- Filter feed by `service_id` query param — **done**

## Future improvements

- Cursor pagination (`before_published_at`, `article_id`)
- Authenticated bookmarks per wallet
- RSS/Atom export for ecosystem readers
- Webhook on new article for subscribers
- Rate limit public feed for abuse protection

## Standards & RFCs

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259), [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#article-store--news-api).

## Depends on

- `article-store`

## Code map

- `backend/app/modules/news/api/routes.py`
- `backend/app/modules/news/services/news_service.py`
