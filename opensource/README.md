# Open Source Components

Extractable modules from the Algorand platform monorepo.

## Packages

| Package | Description | State management |
|---------|-------------|------------------|
| [`wallet_auth_flutter/`](wallet_auth_flutter/) | Algorand wallet login (WC / ARC-0025) | **None** — use Riverpod/Bloc/Provider in your app |
| `pera_flutter_auth/` | Legacy feasibility notes | Superseded by `wallet_auth_flutter` |

## Publishing

Packages are `publish_to: none` while developed locally. When ready, each can move to its own GitHub repository.
