# Brick: Frontend newspaper

## Goal

Show the news feed and article detail in the Flutter web app.

## Status

`done`

## Features (should do)

- List feed from `GET /api/v1/news/feed`
- Show stats from `GET /api/v1/news/stats`
- Pull-to-refresh on feed
- Tap card → `/news/articles/:id` detail
- Display title, summary, body (selectable text)
- Empty and error states with actionable copy
- Front-page layout: full-width lead story, remaining stories in a two-column grid ≥700px (placements span full width)
- Article detail reads as a page: kicker / headline / deck / byline rules, ~720px reading measure, serif markdown body (`core/ui/article_markdown.dart`)

## Metrics on the news page

| Metric | Shown in Flutter today? | Source |
|--------|-------------------------|--------|
| Article count in feed | Yes — subtitle via `newspaperArticleCount` | `GET /api/v1/news/stats` → `article_count` |
| Source kind chips (Discord, Reddit, …) | Yes — from registry | `GET /api/v1/registry/services` + feed `service_id` |
| ALGO spot price | Yes — header ticker via `GET /api/v1/metrics/price` | Workers → Cassandra `price_metrics_brief` |
| 24h change / market cap | No | Same; used in weekly digest articles, not feed header |
| Node count / network health | No | Not implemented; future chain or ops metric |

Weekly market copy appears as a **feed article** (`service_id` `weekly-digest`) after Celery `publish_weekly_digest`, not as a live ticker on the news header.

## Good to have

- Header strip: spot price + 24h change from `GET /api/v1/metrics/price` — **done**
- Relative timestamps for `published_at_epoch` — **done** (feed + detail)
- Service id label on cards — **done** (meta line)
- Link to trigger txn on explorer (TestNet) — **done** (`EXPLORER_BASE_URL`, default Pera TestNet)

## Future improvements

- Infinite scroll with cursor pagination
- `flutter_markdown` rendering with sanitization
- Share article URL
- Offline cache (PWA)
- Push notification when new article for followed services
- Dark mode tuned for long-form reading

## Standards & RFCs

[RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) API JSON; CommonMark rendering (future). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#frontend-newspaper).

## Depends on

- `news-api`, `frontend-shell`, `web-platform`

## Code map

- `frontend_flutter/lib/modules/newspaper/`
