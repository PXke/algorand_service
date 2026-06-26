# Product 2 — Suggestions (overview)

> **Status: paused (2026-06).** The product is disabled by default on both sides:
> frontend hides nav/route unless built with `--dart-define=SUGGESTIONS_ENABLED=true`;
> backend does not register `/api/v1/suggestions*` routes unless `SUGGESTIONS_ENABLED=true`
> (Pydantic setting `suggestions_enabled`). Code and bricks stay in place for re-enabling.

Suggestions is a **product** made of **bricks**. See each brick doc for the three feature lists.

## Goal

TestNet board: pay treasury to post an idea; off-chain signature to upvote.

## Brick list

| Brick | Doc |
|-------|-----|
| `submission-on-chain` | [submission-on-chain.md](submission-on-chain.md) |
| `suggestions-api` | [suggestions-api.md](suggestions-api.md) |
| `suggestions-store` | [suggestions-store.md](suggestions-store.md) |
| `upvote-offchain` | [upvote-offchain.md](upvote-offchain.md) |
| `frontend-suggestions` | [frontend-suggestions.md](frontend-suggestions.md) |

Shared: `chain-read`, `conduit-cassandra`, `wallet-auth`, `frontend-shell`.

## Product-level features (should do)

- Ship with P1 in one Flutter app (shared shell)
- Treasury payment verification before accept
- One upvote per wallet per suggestion

## Product-level good to have

- Wallet-native upvote signing (no paste)
- Public treasury + min amount shown in UI

## Product-level future improvements

- Suggestion expiry, moderation (off-chain), governance export
- On-chain metadata in txn note (optional)

## Config

| Env | Brick |
|-----|--------|
| `PLATFORM_TREASURY_ADDRESS` | `submission-on-chain` |
| `SUGGESTION_MIN_MICROALGOS` | `submission-on-chain` |
