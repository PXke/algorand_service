# Brick: Chain tail watcher

## Goal

Process **new Conduit-indexed rounds** and enqueue newspaper work when txns match the registry.

## Status

`done`

## Features (should do)

- Task `process_new_rounds` (and compat `poll_new_blocks`)
- Redis cursor `chain_tail:last_processed_round`
- Head from `conduit_meta.last_ingested_round` (fallback algod status)
- Process rounds `(last+1)…min(head, last+CHAIN_TAIL_MAX_ROUNDS_PER_RUN)`
- For each match with `scrape_url`: `publish_from_chain_event.delay(...)`
- Celery beat every 30s
- Do **not** require archiving all txns for newspaper (only matched work)

## Good to have

- Log summary: rounds processed, matches enqueued, skipped (no URL)
- Skip disabled services without loading full registry each txn

## Future improvements

- Subscribe to algod websocket instead of polling head
- Per-service rate limits (max N articles per hour)
- Metrics: lag rounds behind head, match rate
- Replay round range tool for backfill after outage
- Filter txn types before registry (e.g. only `pay`/`appl`)

## Standards & RFCs

Algorand round monotonicity; internal Redis cursor keys. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#chain-tail-watcher).

## Depends on

- `conduit-cassandra`, `chain-read`, `service-registry`, `celery-redis-queues`

## Code map

- `workers/app/modules/chain_tail/tasks/watch_blocks.py`
- `workers/app/modules/chain_tail/matching.py`
