# Brick: SEO / crawl surfaces

## Goal

Give crawlers discovery files (sitemaps, RSS, robots, llms.txt) and generated
OG share cards. App HTML is served as a static Vite SPA by nginx — document
HTML SSR was removed.

## Status

`done` (SPA + crawl endpoints)

## Features (should do)

- nginx proxies crawl/meta paths to this module; SPA handles `/`, `/news`, etc.
- Feeds/discovery: `robots.txt`, `sitemap.xml`, `sitemap-pages.xml`, `sitemap-articles-:part`, `sitemap-news.xml`, `feed.xml`, `feed/topic/:tag`, `llms.txt`
- `GET /og/article/:id` share-card PNG
- `POST /api/v1/analytics/pageview` beacon

## Future improvements

- Optional prerender of critical article routes if Search Console needs it
- `dateModified` update on article edit (needs a Cassandra migration)

## Depends on

- `news-api`, `web-platform`

## Code map

- `backend/app/modules/seo/`
