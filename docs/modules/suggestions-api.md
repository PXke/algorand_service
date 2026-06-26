# Brick: Suggestions API

## Goal

HTTP API to create/list suggestions and upvote with session auth.

## Status

`done` — **disabled by default**: routes are only registered when the backend
setting `SUGGESTIONS_ENABLED=true` (see [suggestions.md](suggestions.md)).

## Features (should do)

- `POST /api/v1/suggestions` — session + validated body + on-chain proof
- `GET /api/v1/suggestions` — open list (public)
- `POST /api/v1/suggestions/{id}/upvote` — session + signature
- `GET /api/v1/suggestions/{id}/upvote-message` — canonical sign payload
- JSON errors with `error` code and `detail`

## Good to have

- Request validation messages aligned with OpenAPI-style docs
- `limit` on list endpoint

## Future improvements

- Close/archive suggestion status (admin or author)
- Comments thread per suggestion (new brick)
- Moderation hide (off-chain only)
- Webhook when suggestion reaches vote threshold
- Export CSV for governance

## Standards & RFCs

[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) (401/409/400), [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#suggestions-api--suggestions-store).

## Depends on

- `submission-on-chain`, `suggestions-store`, `upvote-offchain`, `wallet-auth`

## Code map

- `backend/app/modules/suggestions/api/routes.py`
- `backend/app/modules/suggestions/services/`
