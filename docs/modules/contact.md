# Brick: Contact form

## Goal

Give readers a way to reach the platform without exposing an inbox or running
outbound email infra.

## Status

`done`

## Features (should do)

- `POST /api/v1/contact` — public submission, honeypot + per-IP hourly Redis rate limit
- `GET /api/v1/admin/contact-messages` — wallet-gated admin inbox read
- No outbound email — messages are read only via the admin UI
- Month-bucketed Cassandra partitions (`ContactStmts`)

## Good to have

- Reply-by-email from the admin inbox

## Future improvements

- n/a — narrow, complete scope

## Standards & RFCs

n/a.

## Depends on

- Redis (rate limit), `admin` (inbox UI)

## Code map

- `backend/app/modules/contact/`
- `frontend_flutter/lib/modules/admin/ui/inbox_page.dart`
