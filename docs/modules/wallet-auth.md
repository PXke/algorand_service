# Brick: Wallet auth (backend)

## Goal

Wallet-agnostic login: issue challenge, verify proof, issue session token (not Pera-specific).

## Status

`done`

## Features (should do)

- `POST /api/v1/auth/nonce` — SIWA-style message + CAIP-122 payload + nonce in Redis
- `POST /api/v1/auth/verify-wallet-signature` — verify and consume nonce once
- Support **ARC-0060** (`arc0060` proof), **ARC-0025** (`signed_txn_b64`), legacy message signature
- `GET /api/v1/auth/session`, `POST /api/v1/auth/logout`
- Nonce and session TTL via config
- Rate-limit nonce issuance per wallet

## Good to have

- Structured audit log of verify attempts (without storing secrets)
- Wallet address normalization / checksum validation on every endpoint
- Documented test vectors for ARC-0060 interop

## Future improvements

- Session refresh tokens and rotation
- Multi-wallet linking to one user account
- Hardware wallet–specific connector hints in error messages
- OAuth-style delegated scopes per API area (news vs admin)
- Additional [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) official test vectors beyond current sorted-key JSON

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [CAIP-122](https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-122.md), [EIP-4361](https://eips.ethereum.org/EIPS/eip-4361) | SIWA message + JSON payload |
| [ARC-0060](https://arc.algorand.foundation/ARCs/arc-0060), [ARC-0025](https://arc.algorand.foundation/ARCs/arc-0025), [ARC-0001](https://arc.algorand.foundation/ARCs/arc-0001) | Proof methods |
| [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785), [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) | JCS + Ed25519 |

See [wallet-auth-protocol.md](../architecture/wallet-auth-protocol.md) and [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#wallet-auth--wallet-auth-flutter--frontend-auth).

## Depends on

- `session-store` (Redis)
- `web-platform` (browser clients)

## Code map

- `backend/app/modules/auth/`
- `docs/architecture/wallet-auth-protocol.md`
- `docs/adr/ADR-0002-wallet-auth-protocol.md`
