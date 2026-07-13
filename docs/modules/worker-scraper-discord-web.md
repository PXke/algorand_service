# Brick: Worker scraper — Discord (web)

> **REMOVED (2026-07-06).** Discord/Reddit/Telegram + external-push lanes were
> removed in the pipeline simplification — do not restore. No
> `workers/app/modules/discord*` code exists; this doc is kept only so old
> links resolve.

## Goal

Crawl Discord **like a website**: open `https://discord.com/channels/…` in headless Chromium (Playwright), extract visible text — same pattern as SPA web crawl.

## Status

`done` (web mode default)

## Registry URLs

| `scrape_url` | Browser opens |
|--------------|-----------------|
| `discord://channels/GUILD_ID/CHANNEL_ID` | `https://discord.com/channels/GUILD_ID/CHANNEL_ID` |
| `discord://web/https://discord.com/channels/…` | given URL |
| `https://discord.com/channels/GUILD_ID/CHANNEL_ID` | direct |

Legacy `discord://channel/CHANNEL_ID` (bot API only) still works when `DISCORD_SCRAPE_MODE=bot`.

## Modes

| `DISCORD_SCRAPE_MODE` | Scraper |
|-----------------------|---------|
| `web` (default) | `DiscordWebScraper` — Playwright |
| `bot` | `DiscordScraper` — Bot API if `DISCORD_BOT_TOKEN` set |

## Important limitation

**Official Algorand Discord** channel pages usually show a **login wall** without a user session. Web crawl will return `login_required` — use [push-ingest.md](push-ingest.md) or mail for official posts.

Web mode works when:

- A page is genuinely public (invite landing, some marketing pages), or
- You later add authenticated storage (not in v1).

## Docker

Workers image must include Playwright browsers:

```bash
playwright install chromium
```

## Code

- `workers/app/modules/scraper/core/discord_web_scraper.py`
- `workers/app/modules/scraper/core/web_fetch.py`
