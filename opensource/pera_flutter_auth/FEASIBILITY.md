# Pera Flutter Auth Feasibility (Initial)

## Outcome

A direct, official "Pera Flutter SDK" was not identified as a stable maintained package. The practical route is WalletConnect-compatible integration from Flutter.

## Findings

- Community packages exist for Algorand via WalletConnect v1 style integrations.
- WalletConnect Flutter v2 package lineage appears fragmented and some packages are deprecated/archived.
- Deep-link reliability issues are known, especially around iOS/web flow timing.

## Proposed implementation approach

1. Build a platform adapter module at `opensource/pera_flutter_auth`.
2. Start with Flutter web support and nonce-signature authentication.
3. Add robust deep-link handling strategy for mobile as second step.
4. Keep provider abstraction so wallet implementation can be swapped when ecosystem stabilizes.

## Decision gate

- If a maintained package is verified during implementation, use it.
- Otherwise, implement and publish this adapter as an open-source contribution.
