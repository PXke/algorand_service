import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/config/app_config.dart';
import '../wallet_auth_factory.dart';

/// Riverpod adapter only — core logic lives in `wallet_auth_flutter`.
final walletAuthClientProvider = Provider<WalletAuthClient>((ref) {
  final client = createWalletAuthClient(AppConfig.instance);
  ref.onDispose(client.dispose);
  // Fire-and-forget session restore at startup.
  client.restoreSession();
  return client;
});

/// Reactive view of the client's ValueListenable state, so providers that
/// depend on the connected wallet (e.g. admin gating) recompute on login
/// and logout instead of reading a one-shot snapshot.
final walletAuthStateProvider = Provider<WalletAuthState>((ref) {
  final listenable = ref.watch(walletAuthClientProvider).state;
  void onChange() => ref.invalidateSelf();
  listenable.addListener(onChange);
  ref.onDispose(() => listenable.removeListener(onChange));
  return listenable.value;
});
