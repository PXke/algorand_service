# Locale URLs: `?lang=xx` → `/xx/news/articles/slug`

Migrated 2026-07-29. Translated articles moved from a query parameter to a
path segment.

## Why

Search Console evidence (3 months to 2026-07-29) showed the translated pages
are the **best-performing pages on the site** — 28% of all impressions at an
impression-weighted position of 15.8, versus 21.0 for the English pages, with
21 of 27 locale rows in the top 10. The English Algorand-news space is
saturated; the ru/zh/fr/es one is nearly empty.

The query-parameter form was not blocking indexation (767 pages indexed), so
this is an optimisation, not a bug fix. It was still worth doing:

- **Yandex.** Russian is the top-performing locale and Yandex is most of
  Russian search. Yandex handles path segments far more predictably than
  parameters.
- **Link sharing.** Query strings get stripped or normalised by some clients,
  shorteners and unfurlers. The Telegram distribution lane depends on links
  surviving intact.
- **Google formally discourages parameters** — of the accepted i18n URL
  structures, it is the only one their multi-regional guidance advises
  against, and it forfeits folder-level segmentation in Search Console.

Migrating early was deliberate: at ~770 indexed pages and near-zero clicks
there was almost no accumulated equity to put at risk.

## Shape

- English stays at the bare path: `/news/articles/<slug>`.
- Translations are locale-prefixed: `/fa/news/articles/<slug>`.
- **Only article documents are prefixed.** The front page, `/topics`, `/about`
  etc. have no translations, so a prefix there would mint URLs the server
  404s. The reader's locale preference persists in `localStorage`
  (`i18n.localePreference`), so it no longer needs to ride in every URL.
- The old form still resolves: the bare route **301s** `?lang=xx` to the
  locale path, resolving the slug first so it is a single hop rather than a
  chained `id → slug → locale` redirect.

## Code touchpoints

| Concern | Location |
|---|---|
| Canonical path construction | `backend/app/modules/seo/render.py` — `article_path(article_id, slug, lang)` |
| hreflang cluster, sitemap URLs | follow `article_path`/`article_url` automatically |
| Route + legacy 301 | `backend/app/modules/seo/api/routes.py` — `article_localized`, `article`, `_article_document` |
| SPA locale segment | `frontend/src/lib/paths.ts` — `splitLocalePath`, `withLang`, `langFromLocation` |
| SPA canonical | `frontend/src/lib/seo.ts` — `articleCanonicalPath` |
| SPA route match | `frontend/src/App.svelte` |

Analytics deliberately still tracks the **canonical unprefixed path**: those
counters drive per-article view counts and Most Read, and keying them by
locale would split one story's readership across nine URLs.

## nginx

The locale prefix needs its own `location`, added in
`deploy/nginx/algorand-platform.conf`:

```nginx
location ~ ^/(ar|es|fa|fr|hi|ps|ru|zh)/news/articles/ { proxy_pass http://algorand_api; }
```

This **ships with a normal `./deploy/deploy.sh deploy`** — `detect_changes.sh`
sets `DEPLOY_CHANGED_DEPLOY_CONFIG=1` on any `deploy/nginx/*` edit, which makes
`cmd_deploy` re-run `install_nginx_site`. No separate step.

It is a regex rather than nine `^~` blocks because the prefix is one of a fixed
set; regex locations are still evaluated before the `location /` fallback, so
it wins.

**Keep the language set in sync with `ARTICLE_TRANSLATION_LANGS`**
(`backend/app/core/article_translation_langs.py`). A language added there but
not here falls through to `location / { try_files $uri =404; }` and is served
as the SPA shell **with a 404 status** — no SSR, no translated `<title>`, and
a 404 to every crawler. That is strictly worse than the `?lang=` form this
replaced, and it fails silently for that one locale.

## Post-deploy checklist

1. `curl -sI https://algorand.pxke.me/news/articles/<slug>?lang=fr` → **301** to
   `/fr/news/articles/<slug>`
2. `curl -s https://algorand.pxke.me/fr/news/articles/<slug>` → `<html lang="fr">`,
   self-referencing canonical on the locale path, translated `<title>`
3. `curl -s https://algorand.pxke.me/sitemap.xml | grep -c 'lang='` → **0**
4. Resubmit the sitemap in Search Console and Yandex Webmaster.
5. Expect a re-indexing dip for a few weeks — the 301s carry the signal over,
   but Google has to recrawl to see them.
