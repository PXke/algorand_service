# Push ingest — Discord / Telegram without bots

Official Algorand **Discord** and many **Telegram** channels cannot host our bot. Polling those URLs will always fail or return empty. Use **push** lanes instead.

## Primary API

`POST /api/v1/ingest/signal`

Headers:

- `X-Ingest-Key: <INGEST_API_KEY>` (or `Authorization: Bearer <key>`)

Body (JSON):

```json
{
  "service_id": "algorand-foundation-discord",
  "display_name": "Algorand Foundation (Discord mirror)",
  "page_title": "Community call — June 12",
  "page_text": "Full announcement text pasted or forwarded…",
  "source_url": "push://discord/announcement/2026-06-12",
  "source_kind": "push",
  "match_value": "foundation-manual"
}
```

Flow:

1. API validates key → Redis list `algorand:ingest:external_signals`
2. Worker beat `drain_external_ingest_queue` (every 30s) → `ingest_publish_signal()` → publish queue

Same scoring, breaking rules, and daily caps as crawlers.

## Local browser (your PC)

| Tool | Best for |
|------|----------|
| [Firefox channel sync extension](firefox-channel-sync.md) | **Firefox** — extension reads open Discord/Telegram/Reddit tabs |
| [Local browser bridge](local-browser-bridge.md) | Chrome/Chromium with remote debugging (`tools/local_browser_bridge/`) |

Both POST to this API. No server Playwright, no bot on official servers.

## Who can push?

| Bridge | How |
|--------|-----|
| **Your browser** | [local-browser-bridge.md](local-browser-bridge.md) — Chrome + `bridge.py watch` |
| **Foundation workflow** | Zapier/Make, script, or internal tool POSTs on each announcement |
| **Community moderator** | Small forwarder bot in a **bridge server** they control (forwards to our API, not official channel) |
| **Editorial** | curl/Postman until admin UI exists |
| **RSS → push** | Cron reads `algorand.foundation` RSS, POSTs new items (script outside repo) |

## Lanes that do not need Discord/Telegram bots

| Lane | Official Algorand content? |
|------|----------------------------|
| **Mail (IMAP)** | Yes — newsletter / lists |
| **Web registry** | Yes — foundation.com, blog, status |
| **Reddit** | Partial — community mirror |
| **Push API** | Yes — when Foundation or partner sends |
| **Chain + scrape_url** | Partial — on-chain + project site |

## Disable useless pollers

If you have no bot access anywhere:

```bash
# Leave unset — polls no-op
DISCORD_BOT_TOKEN=
TELEGRAM_BOT_TOKEN=
```

Keep `MAIL_IMAP_*` and registry **https://** sources. Use push for Discord-shaped announcements.

## Example (curl)

```bash
curl -sS -X POST http://localhost:8080/api/v1/ingest/signal \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Key: $INGEST_API_KEY" \
  -d '{
    "service_id": "algorand-foundation-announce",
    "display_name": "Algorand Foundation",
    "page_title": "Scam alert",
    "page_text": "Official warning: …",
    "source_url": "push://foundation/scam/1",
    "source_kind": "push"
  }'
```

## Code

- `backend/app/modules/ingest/`
- `workers/app/modules/newspaper/external_ingest_queue.py`
- `workers/app/modules/newspaper/tasks/ingest_tasks.py`

## Env

| Variable | Where | Meaning |
|----------|-------|---------|
| `INGEST_API_KEY` | backend | Required to accept POST |
| `INGEST_QUEUE_DRAIN_SECONDS` | workers | Drain interval (default 30) |
| `REDIS_URL` | backend + workers | Must point at same Redis DB for queue |
