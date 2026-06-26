# Brick: Health & observability

## Goal

Operators can see if the API and critical dependencies are usable before sending traffic.

## Status

`done` (v1)

## Features (should do)

- `GET /health` — process liveness (`status`, `service`, `env`)
- `GET /health/ready` — dependency checks with per-check `ok` and `detail`
- Check Redis (sessions)
- Check Cassandra (query)
- Check Typesense when configured (or `not_configured` = ok)
- Check Conduit index (`conduit_meta.last_ingested_round`)

## Good to have

- Degraded vs failed semantics documented for orchestrators
- Celery worker heartbeat file or queue depth in readiness

## Future improvements

- Prometheus `/metrics` endpoint
- OpenTelemetry traces across API → Celery → Conduit
- Structured JSON logging with request id
- SLO dashboards: chain lag, article publish rate, auth error rate
- PagerDuty runbooks linked from check names

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | `/health` vs `/health/ready` status codes |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#health-observability).

## Depends on

- `session-store`, `cassandra-repository`, `conduit-cassandra`

## Code map

- `backend/app/core/health.py`
- `backend/app/main.py`
