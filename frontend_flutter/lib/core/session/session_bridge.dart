import 'package:flutter/foundation.dart';

/// Lightweight wallet session snapshot kept in the main bundle so the rest of
/// the app (admin gating, API headers) never imports `wallet_auth_flutter`.
class SessionSnapshot {
  const SessionSnapshot({
    this.walletAddress,
    this.sessionToken,
    this.isAuthenticated = false,
    this.isLoading = false,
  });

  final String? walletAddress;
  final String? sessionToken;
  final bool isAuthenticated;
  final bool isLoading;

  static const empty = SessionSnapshot();
}

/// Populated by the deferred auth chunk once `wallet_auth_flutter` loads.
class SessionBridge {
  SessionBridge._();

  static final SessionBridge instance = SessionBridge._();

  final ValueNotifier<SessionSnapshot> notifier =
      ValueNotifier(SessionSnapshot.empty);

  void publish(SessionSnapshot snapshot) {
    notifier.value = snapshot;
  }
}
