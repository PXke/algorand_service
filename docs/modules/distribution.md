# Brick: Social distribution

## Goal

Auto-post every newly published article to the platform's social channels
without a human copy/paste step.

## Status

`done` — Bluesky, Telegram, Mastodon live; Nostr not started

## Features (should do)

- `SocialDistributor` interface (`post_article` / `enabled`), one implementation per channel: `bluesky.py`, `telegram.py`, `mastodon.py`
- `dispatcher.py:distribute()` fans out to all channels, gated per-channel only by presence of credentials (no single master flag), fault-isolated — one channel failing never blocks the others
- Wired into real publish paths via Celery task `distribute_article` (`newspaper/tasks/distribution_tasks.py`), called from `queue_drain_tasks.py` and `publish_tasks.py`
- Not called from recompose/auto-apply by design — avoids re-posting edits

## Good to have

- Nostr distributor (abstract `SocialDistributor` pattern already supports adding it)

## Future improvements

- Per-channel post analytics (click-through back to the article)

## Standards & RFCs

AT Protocol (Bluesky), Telegram Bot API, Mastodon API.

## Depends on

- `article-store` (publish event), owner-provided platform accounts + credentials in `workers.env`

## Code map

- `workers/app/modules/distribution/` (`bluesky.py`, `telegram.py`, `mastodon.py`, `dispatcher.py`)
- `workers/app/modules/newspaper/tasks/distribution_tasks.py`
