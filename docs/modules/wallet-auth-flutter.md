# Brick: wallet_auth_flutter (OSS)

## Goal

Reusable Flutter package for Algorand wallet auth (no Riverpod/Bloc inside the library).

## Status

`done`

## Features (should do)

- `WalletAuthClient`: connect → nonce → sign → verify → session restore
- WalletConnect **ARC-0025** with `algorand=true` on URI
- **ARC-0060** `algo_signData` when wallet supports it; **ARC-0025** `algo_signTxn` fallback with SIWA in note
- Pluggable `SessionStorage` (secure + memory for tests)
- TestNet chain id **416002** configurable
- `HttpAuthApi` against standard backend routes

## Good to have

- Published package versioning and changelog on pub.dev / GitHub
- Example app in `example/` runnable against local API
- `signMessage(String)` API for other features (e.g. upvotes)

## Future improvements

- First-class Defly / Pera deeplink helpers beyond generic WC URI
- Web: in-page QR widget (optional; app may keep dialog)
- Ledger connector implementation behind `WalletConnector`
- Biometric re-auth before using stored session
- WalletConnect v2 session persistence improvements

## Standards & RFCs

Same as `wallet-auth` (client side). [wallet-auth-protocol.md](../architecture/wallet-auth-protocol.md), [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#wallet-auth--wallet-auth-flutter--frontend-auth).

## Depends on

- Backend `wallet-auth` brick

## Code map

- `opensource/wallet_auth_flutter/`
- Consumed: `frontend_flutter/lib/modules/auth/`
