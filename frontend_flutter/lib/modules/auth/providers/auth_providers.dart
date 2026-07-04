import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../../core/config/app_config.dart';
import '../../../core/session/session_bridge.dart';
import '../wallet_auth_factory.dart';

void _syncSession(WalletAuthClient client) {
  final auth = client.state.value;
  SessionBridge.instance.publish(
    SessionSnapshot(
      walletAddress: auth.walletAddress,
      sessionToken: auth.sessionToken,
      isAuthenticated: auth.isAuthenticated,
      isLoading: auth.isLoading,
    ),
  );
}

/// Riverpod adapter only — core logic lives in `wallet_auth_flutter`.
/// Imported via a deferred chunk; mirrors state into [SessionBridge].
final walletAuthClientProvider = Provider<WalletAuthClient>((ref) {
  final client = createWalletAuthClient(AppConfig.instance);
  void onChange() => _syncSession(client);
  client.state.addListener(onChange);
  ref.onDispose(() {
    client.state.removeListener(onChange);
    client.dispose();
    SessionBridge.instance.publish(SessionSnapshot.empty);
  });
  client.restoreSession();
  _syncSession(client);
  return client;
});
