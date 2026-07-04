import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../modules/admin/services/admin_api.dart';
import '../session/session_bridge.dart';
import 'api_providers.dart';

/// Reactive view of [SessionBridge] for admin gating and authenticated API calls.
final sessionStateProvider = Provider<SessionSnapshot>((ref) {
  final bridge = SessionBridge.instance;
  void onChange() => ref.invalidateSelf();
  bridge.notifier.addListener(onChange);
  ref.onDispose(() => bridge.notifier.removeListener(onChange));
  return bridge.notifier.value;
});

final sessionHeadersProvider = Provider<Map<String, String>>((ref) {
  final token = ref.watch(sessionStateProvider).sessionToken;
  if (token == null || token.isEmpty) {
    return const {};
  }
  return {'x-session-token': token};
});

/// AdminApi carrying the current verified session token.
final adminApiProvider = Provider<AdminApi>((ref) {
  final token = ref.watch(sessionStateProvider).sessionToken;
  return AdminApi(ref.watch(apiClientProvider), sessionToken: token);
});
