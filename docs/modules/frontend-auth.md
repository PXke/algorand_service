# Brick: Frontend auth

## Goal

Connect wallet, sign in, and attach session token to API calls in the Vite + Svelte frontend (`frontend/`).

## Status

`done`

## Features (should do)

- `walletProviders.ts`: multi-wallet adapter (Pera / Defly / Lute) — every provider lands on the same connect → nonce → sign → verify flow via `loadWalletAdapter`
- `session.ts`: Svelte stores (`session`, `sessionReady`, `walletFlow`, `authBusy`, `authError`); `signInWithWalletConnect()` drives the flow, `restoreSession()` on startup
- `WalletDialog.svelte`: connect/disconnect UI, wallet picker, pairing/signing status
- Session token persisted in `localStorage` (`wallet_auth_session_token`)
- Pass `x-session-token` on authenticated API calls (`sessionHeaders()` in `api/auth.ts`)

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

- `wallet-auth`, `web-platform`

## Code map

- `frontend/src/lib/auth/` (`session.ts`, `walletProviders.ts`, `pera.ts`, `defly.ts`, `lute.ts`, `walletconnect.ts`)
- `frontend/src/components/WalletDialog.svelte`
- `frontend/src/lib/api/auth.ts`

Note: `opensource/wallet_auth_flutter/` (formerly consumed here via the newspaper's old Flutter frontend) is now only consumed by the separate x402/KYC app (`frontend_kyc/`) — see [wallet-auth-flutter.md](wallet-auth-flutter.md).
