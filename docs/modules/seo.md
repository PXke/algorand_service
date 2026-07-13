# Brick: SEO / SSR surface

## Goal

Give crawlers (Googlebot, Bingbot, social-card fetchers) a real server-rendered
`<title>`/OG/JSON-LD + `#ssr-body` for every navigable path, while humans boot
the same HTML into the normal Flutter app.

## Status

`done`

## Features (should do)

- nginx proxies navigation paths to this module instead of the static Flutter shell
- Document routes: `/`, `/news`, `/news/articles/:id`, `/section/:slug`, `/topic/:tag`, `/about`, `/contact`, `/search`, `/admin` — injects SSR content into `index.html`, mirrors `HEAD` for every `GET`
- Feeds/discovery: `robots.txt`, `sitemap.xml`, `sitemap-pages.xml`, `sitemap-articles-:part`, `sitemap-news.xml`, `feed.xml`, `feed/topic/:tag`, `llms.txt`
- `POST /api/v1/analytics/pageview` beacon, recorded search terms
- `ssr-body` stays render-visible (`aria-hidden` only, not `display:none`) — verified Googlebot's WRS fires `flutter-first-frame` before indexing, so hiding it would strip content from the render path
- Title clamp ~65 chars

## Good to have

- Branded OG cards per article (see newspaper-appeal-backlog)

## Future improvements

- `dateModified` update on article edit (needs a Cassandra migration, not started)
- Bing H1-missing gap on shell-only routes

## Standards & RFCs

Open Graph, JSON-LD/schema.org Article, sitemaps.org protocol.

## Depends on

- `article-store`, `news-api`

## Code map

- `backend/app/modules/seo/` (`chrome.py`, `render.py`, `topics.py`, `analytics_store.py`, `api/routes.py`)
