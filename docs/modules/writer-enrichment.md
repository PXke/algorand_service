# Writer enrichment (for Mistral / templates)

Before an article is composed, workers build a **Writer enrichment** bundle: facts about the service, what changed, and third-party context. That block is passed to Mistral (or informs templates) — readers never see the raw bundle.

## Phases

| Phase | Trigger | Collectors |
|-------|---------|------------|
| **discovery** | First snapshot of a service | Domain HTTPS probe, app store links in HTML, internal DB empty, WHOIS (planned) |
| **update** | Content hash changed | Text diff, domain list diff, “did primary domain change?” |
| **scam_alert** | Scam topic (e.g. Foundation Discord warning) | Domains in alert + linked X posts + domain probe |

## What we collect today

| Signal | Status |
|--------|--------|
| HTTPS + security headers | `domain_probe` (HEAD/GET) |
| Mobile app store links in page | `app_stores` (parse only) |
| Prior articles on platform | `internal` + `platform_search` |
| On-chain match metadata | `chain` (tx stats TBD) |
| **X/Twitter URLs in ingest text** | `social_posts` — **oEmbed** (no API key) |
| WHOIS / registrant company vs individual | Planned (RDAP API) |
| Discord/Telegram “good or bad” sentiment | Mirrored ingest + search (not live scrape) |
| X keyword search for “service XYZ” | Planned (API or curator push) |
| Threads / Mastodon | Planned |

## Example: community X post (algoblow)

When Discord/Telegram mirror or push ingest includes:

`https://x.com/d13_co/status/2060386210732761317`

enrichment calls Twitter **oEmbed** and adds plain text to the writer prompt.

**Fixture:** `workers/tests/fixtures/algoblow_d13_alert.txt` (community report with `algoblow[.]com`, `$BLOW` opt-in rekey scam, four cited Algorand accounts). Ingest that text → `scam_alert` + breaking + writer bundle lists domains and addresses for Mistral.

That tweet is independent of Foundation Discord and is exactly the kind of cross-source evidence we want before publishing.

## Configuration

```bash
WRITER_ENRICHMENT_ENABLED=1
WRITER_ENRICHMENT_PROBE_DOMAIN=1
WRITER_ENRICHMENT_FETCH_TWEETS=1
WRITER_EDITORIAL_BRIEFS_ENABLED=1
```

## Editorial briefs (admin suggestion box)

Queued rows in `editorial_briefs` (migration `015`) whose **keywords** match ingest text are appended to the writer prompt as “editorial direction” — not final copy. Create briefs in the Flutter **Admin → Writer briefs** tab or `POST /api/v1/admin/briefs`.

Code: `workers/app/modules/newspaper/editorial_briefs.py`

## Storage

`service_intelligence` (migration `014`) — last primary domain + domain list for update diffs.

## Code

- `workers/app/modules/newspaper/writer_enrichment/`
- Hook: `publish_from_queued_row` → `gather_writer_enrichment` → `format_enrichment_for_writer` → `compose_scrape_article(..., enrichment_block=...)`

## Social sources policy

| Source | Approach |
|--------|----------|
| **URL in trusted ingest** | oEmbed fetch (X status links) |
| **Official Discord/Telegram** | Firefox extension / push — not server scrape |
| **X search / timeline** | Not implemented — use API tier or manual `ingest/signal` with tweet URL |
| **Threads** | Same pattern as X when oEmbed/API available |

See also [scam-article-enrichment.md](scam-article-enrichment.md), [firefox-channel-sync.md](firefox-channel-sync.md).
