# Firefox channel sync (extension)

## What you asked for

You already visit Discord, Telegram, and Reddit in **Firefox** with **your account**. The platform should reuse that — not run headless crawlers or read your profile database off disk.

A **small Firefox extension** does exactly that:

1. You open the channels you care about (same tabs as today).
2. When a tab URL matches a rule you configured, the extension reads **visible text** from the page (your view, your permissions).
3. If content changed since last sync, it `POST`s to `/api/v1/ingest/signal`.
4. Workers enqueue and publish like any other push source.

This is **mirror sync**, not scraping: no bot, no bypass, no access beyond what you can read in the browser.

## vs other options

| Method | Notes |
|--------|--------|
| **Firefox extension** (recommended for you) | Runs only in Firefox; uses your session in-tab |
| Local CDP bridge (`tools/local_browser_bridge`) | Chrome/Chromium with `--remote-debugging-port` |
| Server Playwright | Fails on official Discord login |
| Reading Firefox profile on disk | **Do not** — brittle and invasive |

## Install

See [extensions/algorand-channel-sync/README.md](../../extensions/algorand-channel-sync/README.md).

1. `about:debugging` → Load Temporary Add-on → `manifest.json`
2. Options: API URL, ingest key, URL prefix per channel
3. Browse channels normally; optional **Sync open tabs now** in options

## Data sent

- Text snapshot of the visible channel (title + body text)
- `source_kind`: `firefox_extension`
- Same Redis queue as [push-ingest.md](push-ingest.md)

Screenshots are **not** sent by default (text is enough for the newspaper pipeline).

## Security

- API key stored in `browser.storage.sync` (Firefox Sync if enabled) — treat as secret
- Only configured URL prefixes are matched
- Production API: use HTTPS; add your API host to extension permissions if not localhost

## Registry

Each rule needs a `service_id` that exists in the platform service registry (same as crawlers).

## Publishing

Still subject to priority queue and **7 standard articles / UTC day** (and breaking caps). Sync only **feeds** the ingest queue; it does not bypass editorial limits.

**Scam / safety warnings** (e.g. Foundation `@everyone` posts about malicious sites like `algoblow.com`) are often **Discord- or Telegram-only**. Once synced, the pipeline classifies them as `scam_alert` + **breaking** tier (up to 2 breaking posts/day, no 3h spacing) so they are not buried behind routine blog crawls.

Example manual push if you paste the message:

```bash
curl -sS -X POST "$API/api/v1/ingest/signal" \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Key: $INGEST_API_KEY" \
  -d '{
    "service_id": "algorand-foundation-discord",
    "display_name": "Algorand Foundation (Discord)",
    "page_title": "WARNING: algoblow.com malicious app",
    "page_text": "@everyone WARNING DO NOT interact with algoblow.com! …",
    "source_url": "local://discord/foundation-warning-2026-05-29",
    "source_kind": "firefox_extension"
  }'
```
