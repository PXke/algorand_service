import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../modules/admin/services/admin_api.dart';
import '../../modules/auth/providers/auth_providers.dart';
import '../api/api_client.dart';

final apiClientProvider = Provider<ApiClient>((ref) => ApiClient());

final sessionHeadersProvider = Provider<Map<String, String>>((ref) {
  final token = ref.watch(walletAuthStateProvider).sessionToken;
  if (token == null || token.isEmpty) {
    return const {};
  }
  return {'x-session-token': token};
});

/// AdminApi carrying the current verified session token, so admin calls are
/// authorized server-side by the signed-in wallet (not a spoofable header).
final adminApiProvider = Provider<AdminApi>((ref) {
  final token = ref.watch(walletAuthStateProvider).sessionToken;
  return AdminApi(ref.watch(apiClientProvider), sessionToken: token);
});
