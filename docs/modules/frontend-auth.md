# Brick: Frontend auth

## Goal

Connect wallet, sign in, and attach session token to API calls in the Flutter app.

## Status

`done`

## Features (should do)

- Riverpod `walletAuthClientProvider` wrapping `wallet_auth_flutter`
- `WalletAuthPanel`: connect / disconnect / show address
- WalletConnect URI dialog (copy, open wallet, done)
- Restore session on startup from secure storage
- Pass `x-session-token` on authenticated API calls (suggestions)

## Good to have

- Show auth errors from WC and API clearly
- Loading state on panel during connect/sign

## Future improvements

- QR code widget in dialog (not only text URI)
- “Sign in required” guards on routes with redirect
- Account menu: copy address, view on explorer
- Session expiry warning before TTL ends

## Standards & RFCs

Client flows must match backend: CAIP-122, ARC-0060 / ARC-0025. [wallet-auth-protocol.md](../architecture/wallet-auth-protocol.md), [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#wallet-auth--wallet-auth-flutter--frontend-auth).

## Depends on

- `wallet-auth`, `wallet-auth-flutter`, `web-platform`

## Code map

- `frontend_flutter/lib/modules/auth/`
- `opensource/wallet_auth_flutter/`
