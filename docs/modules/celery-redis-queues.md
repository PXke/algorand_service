# Brick: Celery + Redis queues

## Goal

Run async work (chain tail, scrape, publish, search index) with Redis as broker only.

## Status

`done`

## Features (should do)

- Celery app with JSON serialization
- Queues: `default`, `chain`, `pipeline`, `scrape`, `security`
- Task routes for `chain_tail`, `newspaper`, `search`, `scrape`, `security`
- Beat schedule: `process_new_rounds` every 30s; `collect_price_metrics` (`PRICE_METRICS_POLL_SECONDS`, default 3600s); `publish_weekly_price_analysis` / `publish_weekly_digest` (Monday 09:00 UTC); `check_and_publish_mistral_on_diff` (`MISTRAL_DIFF_POLL_SECONDS`, default 600s)
- systemd units for worker and beat

## Good to have

- Document queue → brick mapping in ops runbook
- Separate Redis DB index for broker vs sessions

## Future improvements

- Flower or Redis queue monitoring UI
- Dead-letter queue and automatic retry policy per task type
- Autoscale workers on queue depth (K8s HPA)
- Priority lanes for user-facing vs batch work
- Task idempotency keys stored in Redis

## Standards & RFCs

[Celery](https://docs.celeryq.dev/) task routing; Redis broker protocol. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#celery-redis-queues).

## Depends on

- Redis broker URL in worker `.env`

## Code map

- `workers/app/celery_app.py`
- `workers/app/tasks/`
- `deploy/systemd/algorand-platform-celery.service`
- `deploy/systemd/algorand-platform-celery-beat.service`
