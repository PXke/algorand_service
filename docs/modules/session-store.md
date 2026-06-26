# Brick: Session store

## Goal

Redis-backed nonces and sessions for wallet auth (short-lived, not Cassandra).

## Status

`done`

## Features (should do)

- Store nonce challenge JSON per wallet with TTL
- Delete nonce on successful verify (one-time use)
- Store session record per token with TTL
- Lookup and delete session on logout
- Enforce per-wallet nonce rate limit (requests per minute)

## Good to have

- Namespaced Redis key prefix per environment (`staging:`, `prod:`)
- Metrics: active sessions, nonce failures

## Future improvements

- Session device fingerprint / user-agent metadata
- Force logout all sessions for a wallet (admin)
- Redis Cluster / Sentinel configuration docs
- Encrypt session payload at rest in Redis

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| Redis TTL semantics | Nonce + session keys |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | Opaque bearer session token |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#session-store).

## Depends on

- Redis reachable from API

## Code map

- `backend/app/modules/auth/services/session_store.py`
