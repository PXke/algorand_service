# Firefox extension — Channel Sync

Sync **what you already see** in Firefox (Discord, Telegram web, Reddit) to the Algorand Platform ingest API. This is **not** crawling: it only runs when **you** have those tabs open, logged in as yourself.

## Why an extension (not reading Firefox profile files)?

| Approach | Verdict |
|----------|---------|
| Read `places.sqlite` / cookies on disk | Fragile, locked while Firefox runs, security risk, breaks on updates |
| Chrome remote debugging (CDP) | Works but awkward; Firefox users need a different flow |
| **WebExtension** | Standard pattern: content script reads the live page DOM in your session |

## Install (temporary — development)

1. Firefox → `about:debugging` → **This Firefox** → **Load Temporary Add-on**
2. Select `extensions/algorand-channel-sync/manifest.json`
3. Open **Extension options** and set:
   - API base (e.g. `http://localhost:8080`)
   - `INGEST_API_KEY` (same as backend)
   - One rule per channel (paste URL from the address bar)

4. Browse Discord / Telegram / Reddit as usual. The extension polls open tabs and when you switch tabs.

## Configure rules

Example prefixes (copy from your address bar when the channel is open):

| Channel | URL prefix example |
|---------|-------------------|
| Discord | `https://discord.com/channels/GUILD_ID/CHANNEL_ID` |
| Telegram web | `https://web.telegram.org/k/#@algorand` |
| Reddit | `https://www.reddit.com/r/Algorand/` |

`service_id` must match a service in the platform registry.

## Server side

Unchanged: `POST /api/v1/ingest/signal` → Redis → worker drain → publish queue.

`source_kind`: `firefox_extension` (same trust tier as push / local browser).

## Permissions

The extension requests host access for Discord, Telegram, Reddit, and your API URL (add production API host in options; for localhost, default manifest includes dev — extend `manifest.json` `permissions` for production domains if needed).

## Package for Firefox Add-ons (later)

Sign with Mozilla AMO or distribute unsigned via enterprise policy. Temporary load is enough for a single laptop.

## See also

- [docs/modules/firefox-channel-sync.md](../../docs/modules/firefox-channel-sync.md)
- [docs/modules/push-ingest.md](../../docs/modules/push-ingest.md)
