# ADR-0002: Wallet Auth Protocol

## Status
Accepted

## Context
We need wallet authentication that works across Algorand wallets (Pera, Defly, etc.), not a single vendor SDK.

## Decision
1. Use **WalletConnect (ARC-0025 / `algo_signTxn`)** as the primary wallet transport in Flutter.
2. Use a **0-ALGO self-payment** with `note` = canonical signing message for login proof (widely supported today).
3. Keep backend verification dual-path:
   - `signed_txn_b64` (WalletConnect path)
   - `signature_b64` over UTF-8 message (future ARC-0060 / raw signing path)
4. Canonical message format is SIWA-inspired plain text with domain + nonce.

## Consequences
- Compatible with most mobile Algorand wallets today.
- ARC-0060 `signData` can be added later without breaking the connector abstraction.
- Reown AppKit is not used because first-class Algorand support is limited; community WC v1 stack is the practical choice.

## Full documentation
See [wallet-auth-protocol.md](../architecture/wallet-auth-protocol.md) for coverage matrices, sequence diagrams, and ARC-0025 / ARC-0060 comparison.
