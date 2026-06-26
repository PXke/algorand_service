# Brick: Frontend suggestions

## Goal

TestNet board: submit ideas (txid) and upvote from the web app.

## Status

`done` — **disabled by default**: nav item and `/suggestions` route only appear when
built with `--dart-define=SUGGESTIONS_ENABLED=true` (see [suggestions.md](suggestions.md)).

## Features (should do)

- List open suggestions from API (includes `upvote_count`)
- Form: title, body, submission txid (with validation hints)
- Require wallet session for submit and upvote
- Upvote: fetch signing message dialog; copy to clipboard; submit `signature_b64`
- Show treasury address and min ALGO via `GET /api/v1/suggestions/config`
- Show API errors in snackbar (structured `{ error: { code, message } }`)

## Example: TestNet submit flow

1. Sign in with wallet (`frontend-auth`).
2. Send ≥ `min_algo_display` ALGO to `treasury_address` from `/api/v1/suggestions/config`.
3. Submit suggestion with the payment txid.
4. Tap **Prepare upvote** → copy signing message → sign in wallet → paste base64 signature → **Submit upvote**.

## Future improvements

- In-wallet sign via `wallet_auth_flutter.signMessage()` (no paste)
- Deep link to create payment txn in Pera/Defly
- Sort/filter suggestions
- Author-only edit before first upvote
- Real-time list refresh (polling or SSE)

## Standards & RFCs

`upvote-offchain` message + `wallet-auth` session header. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md).

## Depends on

- `suggestions-api`, `frontend-auth`, `frontend-shell`, `web-platform`

## Code map

- `frontend_flutter/lib/modules/suggestions/`
- `backend/app/modules/suggestions/api/routes.py` — includes `/config`
