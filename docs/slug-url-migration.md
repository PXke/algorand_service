# Article slug URLs — migration plan

**Status:** not started. Backups taken 2026-07-28. Everything below is verified
against production unless marked "assumed".

**Goal:** replace `/news/articles/<uuid>` with `/news/articles/<slug>`, where the
slug is derived from the title and de-duplicated with a numeric suffix
(`-2`, `-3`, …). Google's SEO starter guide asks for URLs that carry words
rather than IDs; this is the last substantive gap in that audit.

The risk here is not breaking the site — it is breaking it *invisibly*. A
half-applied URL migration still serves pages; it just quietly sheds the
indexing the site has accumulated, and you find out in Search Console weeks
later. Land it in one pass, verify the redirects before anything else, and do
not ship a state where some paths are slugs and some are UUIDs.

---

## 1. Backups (done)

Two independent restore paths, both verified readable — not merely non-empty.

| Kind | Location | Contents |
|---|---|---|
| CSV export | `/home/guillaume/backups/cassandra-20260728T090610Z/` (22 MB) | `articles_by_id` 224 rows, `articles_feed` 90, `article_versions` 438, `deleted_articles` 8, `article_view_counts` 7, plus `schema.cql` (2200 lines) |
| Snapshot | tag `pre-slug-migration-20260728T090649Z` | Engine-level snapshot of the whole `algorand_platform` keyspace |

Restore from CSV, per table:

```sql
COPY algorand_platform.articles_by_id FROM '<dir>/articles_by_id.csv' WITH HEADER=true;
```

**Connection facts** (these cost time to rediscover):

- Keyspace is **`algorand_platform`**, *not* `algorand`.
- Credentials are `CASSANDRA_USERNAME` / `CASSANDRA_PASSWORD` in
  `/home/guillaume/algorand-platform/shared/backend.env` on prod. Note the
  spelling — it is `USERNAME`, not `USER`.
- Host is `127.0.0.1`; cqlsh needs it passed explicitly.

---

## 2. Current state

- `articles_by_id` holds **224** rows; `articles_feed` holds **90**. Slug all
  224 — the extra 134 are older articles whose URLs are indexed and must keep
  resolving.
- `deleted_articles` (8 rows) drives 410 Gone tombstones. That path must
  survive; see invariants.
- URLs today are `/news/articles/<uuid>`, built by `article_path()`.

---

## 3. Design

**Slug source:** the article title, lowercased, non-alphanumerics collapsed to
`-`, trimmed, clamped to ~70 chars on a word boundary.

**Collisions:** append `-2`, `-3`, … Assign at publish time against the lookup
table, and never reassign an existing slug — a slug that has been served is a
permanent URL.

**Storage:** add `slug` to `articles_by_id`, plus a lookup table:

```sql
CREATE TABLE articles_by_slug (
  slug   text PRIMARY KEY,
  article_id uuid
);
```

The lookup table is what makes slug → article an exact single-partition read
rather than a scan. Do not try to resolve slugs by querying `articles_by_id`.

**Why not `<slug>-<uuid>`:** it needs no migration and was the tempting
shortcut, but it still puts an ID in the URL, which is the thing the guide
asks you to remove. Rejected deliberately.

**Migration files:** this repo does not auto-glob CQL migrations — a new
migration needs an entry in `manifest.toml`. Also: no semicolons inside CQL
comments, the splitter cuts on them.

---

## 4. Order of work

Each step should be independently verifiable. Do not batch them.

1. **Migration + manifest entry**: `slug` column on `articles_by_id`,
   `articles_by_slug` table.
2. **`slugify()` + collision assignment**, with unit tests covering: unicode
   titles, titles that collapse to empty (fall back to the uuid), duplicates
   producing `-2`/`-3`, and the length clamp landing on a word boundary.
3. **Backfill all 224 rows.** Deterministic order (by `published_at`) so a
   re-run assigns the same suffixes. Write both the column and the lookup
   table. Verify: 224 slugs, 0 empty, 0 duplicates.
4. **Wire generation into publish** so new articles get a slug at creation.
5. **Resolver**: route accepts a slug, falls back to a uuid.
6. **301 uuid → slug**, permanent. This is the step that protects the existing
   index; verify it against real indexed URLs before continuing.
7. **Rewire the URL builders** (see touch points), so canonical, sitemap, RSS
   and OG all emit slugs consistently. A mixed state here is the failure mode
   worth avoiding.
8. **Frontend** `articleHref()`.
9. **Resubmit** `sitemap.xml` in Search Console.

---

## 5. Touch points

`article_path()` — `backend/app/modules/seo/render.py:270` — and its call sites:

| File | Line | Use |
|---|---|---|
| `seo/sitemap.py` | 261 | `<loc>` |
| `seo/feeds.py` | 47 | RSS link / guid |
| `seo/render.py` | 277 | `article_url()` base |
| `seo/render.py` | 288, 300 | hreflang alternates |
| `seo/render.py` | 425 | translation picker |
| `seo/render.py` | 452, 673, 696 | feed/related links |
| `seo/render.py` | 507 | article canonical |
| `seo/render.py` | 639 | ItemList JSON-LD |

Route handler: `backend/app/modules/seo/api/routes.py:238`.
Frontend: `articleHref()` in the SPA (`frontend/src/lib/paths.ts`).

---

## 6. Invariants — do not regress

- **Tombstones keep returning 410.** `routes.py` checks `_article_tombstoned()`
  and returns 410 for deliberately deleted articles so Google drops them
  promptly instead of retrying a 404 for months. Both the slug path *and* the
  legacy uuid path must preserve this.
- **`?lang=` survives the rewrite.** Translated articles canonicalise to
  `/news/articles/<slug>?lang=fa` and carry hreflang alternates; the 301 must
  keep the query string.
- **Slugs are permanent.** Retitling an article must not change its URL. If a
  slug must ever change, the old one 301s to the new one — never drop it.
- **`/topic/<tag>` keeps using raw slugs**, not display labels. Unrelated to
  this migration but adjacent, and easy to break by accident.
- **`sitemap-articles-:part` and `/og/article/:id`** have no file extension and
  are on the service worker's `navigateFallbackDenylist` by route shape
  (`vite.config.ts`). If article URLs change shape, re-check those patterns.

---

## 7. Verification checklist

Before calling it done:

- [ ] 224 slugs assigned, none empty, none duplicated
- [ ] A known old uuid URL 301s to its slug, preserving `?lang=`
- [ ] A tombstoned article still returns 410 on **both** path forms
- [ ] `canonical`, `<loc>` in sitemap, RSS `guid` and OG all agree — same URL
      form everywhere, no mixed state
- [ ] `curl` a slug URL and confirm SSR HTML, not the SPA shell
- [ ] Backend + workers suites green; `sync_tokens.py` clean
- [ ] Search Console: sitemap resubmitted

---

## 8. Rollback

Revert the URL-builder commit and redeploy — the slug column and lookup table
are additive and harmless if unused, so there is no need to undo the schema.
Only restore from backup if the backfill itself corrupted `articles_by_id`,
which it should not, since it only writes a previously-absent column.
