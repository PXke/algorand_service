# Brick: Worker scraper — Discord

## Goal

Crawl **two (or more) Discord channels** registered in `service_registry` and feed the newspaper pipeline (snapshots → diff → articles).

## Status

**Web mode (default):** [worker-scraper-discord-web.md](worker-scraper-discord-web.md) — Playwright on `discord.com/channels/…`.

**Bot mode:** set `DISCORD_SCRAPE_MODE=bot` + `DISCORD_BOT_TOKEN` (requires bot invite).

Official Algorand Discord usually shows a **login wall** on web — use [push-ingest.md](push-ingest.md) when web crawl fails.

## Features (should do)

- `scrape_url` scheme `discord://channel/<id>` or `discord:<id>`
- `DiscordScraper` via [Discord REST API](https://discord.com/developers/docs/reference) (`/channels/{id}/messages`)
- `DISCORD_BOT_TOKEN` env; bot must have **Read Message History** in each room
- Celery beat `poll_discord_sources` every `DISCORD_POLL_SECONDS` (default 300s)
- Seed template: `deploy/seeds/discord_services.toml` (two rooms)
- Chain-triggered publish still works if `scrape_url` is Discord and a matching txn fires

## Good to have

- Thread / forum channel support
- Rate-limit backoff (Discord 429) — **implemented** (`http_retry.py`)
- Per-room `DISCORD_MESSAGE_LIMIT` in registry metadata

## Future improvements

- Gateway websocket for live updates instead of poll
- Attachment / image OCR
- Moderation filters before article compose

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | Bot HTTP client |
| [Discord API](https://discord.com/developers/docs/reference) | Channel messages |

## Depends on

- `worker-scraper`, `service-registry`, `celery-redis-queues`, `article-compose`

## Code map

- `workers/app/modules/scraper/core/discord_scraper.py`
- `workers/app/modules/scraper/core/discord_urls.py`
- `workers/app/modules/scraper/core/factory.py`
- `workers/app/modules/scraper/tasks/discord_poll_tasks.py`
- `deploy/seeds/discord_services.toml`

## Setup

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications); copy the token → `DISCORD_BOT_TOKEN`.
2. Invite the bot to your server (OAuth2 → `bot` scope, permissions: View Channel, Read Message History).
3. Enable Developer Mode in Discord → right-click each room → **Copy Channel ID**.
4. Edit `deploy/seeds/discord_services.toml` (`REPLACE_CHANNEL_ID_1`, `REPLACE_CHANNEL_ID_2`).
5. Seed: `SEED_FILE=deploy/seeds/discord_services.toml python3 deploy/scripts/seed_service_registry.py`
6. Ensure Celery **worker + beat** run with the same env (see `workers/.env.example`).

Manual poll:

```bash
cd workers && PYTHONPATH=. celery -A app.celery_app call app.tasks.scrape.poll_discord_sources
```
