# wallet_auth_flutter

Wallet-agnostic Algorand authentication for Flutter.

## Design goals

- **No Riverpod / Bloc / GetX** in this package
- **Pluggable** wallet connector, auth API, and session storage
- **Reusable** in any Flutter app or published to pub.dev later

## Core types

| Type | Role |
|------|------|
| `WalletAuthClient` | Orchestrates connect → sign → verify → session |
| `WalletAuthState` | Immutable state (`ValueNotifier` on client) |
| `WalletConnector` | Wallet transport abstraction |
| `WalletConnectAlgorandConnector` | ARC-0025 WC + ARC-0060 `algo_signData`, SIWA/CAIP-122 |
| `AuthApi` / `HttpAuthApi` | Backend contract + HTTP default |
| `SessionStorage` | Token persistence abstraction |

## Usage without Riverpod

```dart
final config = WalletAuthConfig(apiBaseUrl: 'https://api.example.com');
final client = WalletAuthClient(
  config: config,
  authApi: HttpAuthApi(config: config),
  walletConnector: WalletConnectAlgorandConnector(config: config),
  sessionStorage: SecureSessionStorage(),
);

await client.restoreSession();

// In your widget tree:
ValueListenableBuilder<WalletAuthState>(
  valueListenable: client.state,
  builder: (context, auth, _) {
    if (auth.isAuthenticated) {
      return Text(auth.walletAddress!);
    }
    return ElevatedButton(
      onPressed: () => client.connectAndSignIn(
        onDisplayUri: (uri) => showWalletUriDialog(context, uri),
      ),
      child: const Text('Connect wallet'),
    );
  },
);
```

## Usage with Riverpod (thin app adapter)

Keep Riverpod in **your app**, not in this package:

```dart
final walletAuthClientProvider = Provider<WalletAuthClient>((ref) {
  final config = WalletAuthConfig(apiBaseUrl: 'https://api.example.com');
  final client = WalletAuthClient(
    config: config,
    authApi: HttpAuthApi(config: config),
    walletConnector: WalletConnectAlgorandConnector(config: config),
    sessionStorage: SecureSessionStorage(),
  );
  ref.onDispose(client.dispose);
  unawaited(client.restoreSession());
  return client;
});

// UI: ref.watch(walletAuthClientProvider) + ValueListenableBuilder on client.state
```

## Custom backends

Implement `AuthApi` for non-HTTP transports. Implement `WalletConnector` for ARC-0060 `signData` when ready.

## License

MIT (when published)
