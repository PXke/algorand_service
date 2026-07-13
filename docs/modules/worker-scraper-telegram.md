# Brick: Worker scraper — Telegram (web)

> **REMOVED (2026-07-06).** Discord/Reddit/Telegram + external-push lanes were
> removed in the pipeline simplification — do not restore. No
> `workers/app/modules/telegram*` code exists; this doc is kept only so old
> links resolve.

## Goal

Crawl **public** Telegram announcement channels like a normal website via `https://t.me/s/{username}` — **no bot** required.

## Status

`done` (web mode default)

## Registry URLs

| `scrape_url` | Loads |
|--------------|--------|
| `telegram://s/algorand` | `https://t.me/s/algorand` |
| `telegram://@algorand` | same |
| `https://t.me/s/algorand` | same |

## Modes

| `TELEGRAM_SCRAPE_MODE` | Scraper |
|------------------------|---------|
| `web` (default) | `TelegramWebScraper` — HTTP + HTML parse |
| `bot` | `TelegramScraper` — Bot API (`TELEGRAM_BOT_TOKEN`) |

## Limits

- **Public channels only** with preview enabled; private channels fail parse.
- Optional `TELEGRAM_WEB_PLAYWRIGHT=1` if static HTML is empty.

## Code

- `workers/app/modules/scraper/core/telegram_web_scraper.py`
- `workers/app/modules/scraper/core/telegram_urls.py`

## Related

- [worker-scraper-discord.md](worker-scraper-discord.md) (Discord web)
- [push-ingest.md](push-ingest.md) (when web preview is blocked)
