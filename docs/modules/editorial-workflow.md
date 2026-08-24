# Editorial workflow

The Algorand Platform news feed is **editorial** — not raw crawl logs. The pipeline:

1. **Crawl**: HTTP, mail, Telegram, Discord (mirrored), Reddit, chain events, metrics.
2. **Match**: service profiles, scam alerts, events, news.
3. **Enrich**: domain probe, app stores, internal search, X oEmbed, editorial briefs.
4. **Write/edit**: Mistral or template composes markdown; edits append an **Updated** section.
5. **Admin**: wallet-gated markdown editor, version history, suggestion box.

## 1. Ingest

Sources:
- **Push ingest**: Firefox extension, local CDP bridge, or `POST /api/v1/ingest/signal` (auth: `X-Ingest-Key`).
- **Crawlers**: HTTP, mail, Telegram, Discord (mirrored), Reddit, chain events, metrics.

Each ingest signal carries:
- `source_kind` + `service_id` (e.g. `discord:algorand-foundation:announcements`)
- `page_text` + `page_title`
- `publish_mode`: `create` (default) or `edit`
- `linked_article_id`: UUID of the article to edit (only for `edit` mode)
- `match_kind` + `match_value`: dedupe keys (domain, Algorand address, keyword, etc.)

## 2. Matching

`resolve_publish_mode` (`article_matching.py`) decides edit vs create:
- An explicit `publish_mode=edit` + `linked_article_id` (editorial-brief refreshes, admin recompose) wins while the article is still within its edit window.
- Everything else composes as `create`.
- 2026-08-24: dropped crawl-based match-key follow-up detection (domain/keyword/Algorand-address/service_id continuity, `article_match_keys` table) — real prod data showed it fired legitimately twice ever against 1,657 total candidates, both over a month old at removal time; lower crawl volume and pervasive per-source cooldowns mean the scenario it existed for essentially doesn't happen anymore.

## 3. Publish

### Create
- `publish_from_queued_row` → `compose_scrape_article` → `insert_article` → Typesense index.
- Daily caps: 7 standard, 2 breaking.
- Breaking articles skip the queue and publish immediately.

### Edit
- `publish_mode=edit` → `run_article_edit`:
  1. Saves prior body to `article_versions` (version +1).
  2. Calls `compose_article_edit` (Mistral or template) to append an **Updated** section.
  3. Updates `articles_by_id` + `articles_feed` at the original `published_at` timestamp.
  4. Adds an `updated` tag.
- Edit window: 24 hours (configurable via `ARTICLE_EDIT_WINDOW_HOURS`).
- **Does not consume a new daily slot** — edits are free.

## 4. Writer enrichment

Before composition, workers gather:
- Domain probe (HTTPS, security headers)
- App store links in page
- Prior articles on platform (internal search)
- On-chain match metadata (planned)
- **X/Twitter URLs in ingest text** → oEmbed fetch (no API key)
- **Editorial briefs**: queued admin suggestions whose keywords match ingest text

Enrichment block is passed to Mistral or templates — never shown raw to readers.

## 5. Admin UI

Flutter **Admin → Articles** tab:
- Load article by UUID from public news API.
- Edit title, summary, body (markdown).
- Save via `PATCH /api/v1/admin/articles/:id` (auth: `X-Admin-Wallet`).

**Admin → Writer briefs** tab:
- Create/list suggestion-box briefs for the writer agent.
- Keywords determine which ingest signals trigger the brief.

## 6. End-to-end example: algoblow

1. **Ingest v1** (Foundation Discord warning):
   ```bash
   POST /api/v1/ingest/signal
   {
     "source_kind": "discord",
     "service_id": "algorand-foundation:announcements",
     "page_text": "Warning: algoblow.com is a scam.",
     "page_title": "Scam alert",
     "publish_mode": "create"
   }
   ```
   → Creates article `abc123` with match keys `domain:algoblow.com`, `keyword:$BLOW`.

2. **Ingest v2** (community X post):
   ```bash
   POST /api/v1/ingest/signal
   {
     "source_kind": "x",
     "service_id": "x:d13_co",
     "page_text": "Victims of algoblow.com: ALGO...123, ALGO...456. See https://x.com/d13_co/status/2060386210732761317",
     "page_title": "algoblow victim addresses",
     "publish_mode": "edit",
     "linked_article_id": "abc123"
   }
   ```
   → Matches `domain:algoblow.com` → edits `abc123` → saves v1 body to `article_versions` → appends **Updated** section with victim addresses.

3. **Admin edit** (optional):
   - Load `abc123` in **Admin → Articles** tab.
   - Fix typo → save → new version + `updated` tag.

## 7. Storage

| Table | Purpose |
|-------|---------|
| `article_versions` | Version history (body snapshots) |
| `editorial_briefs` | Admin suggestion box for writer |

## 8. Configuration

```bash
# Backend
ADMIN_WALLET_ADDRESSES=your_wallet_address
INGEST_API_KEY=your_ingest_key

# Workers
WRITER_ENRICHMENT_ENABLED=1
WRITER_EDITORIAL_BRIEFS_ENABLED=1
ARTICLE_EDIT_WINDOW_HOURS=24
```