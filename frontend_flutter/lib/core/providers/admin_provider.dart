import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/app_config.dart';
import '../../modules/auth/providers/auth_providers.dart';

/// Incremented after a successful admin pipeline reset so other tabs reload.
final adminPipelineResetSignalProvider =
    NotifierProvider<AdminPipelineResetSignal, int>(AdminPipelineResetSignal.new);

class AdminPipelineResetSignal extends Notifier<int> {
  @override
  int build() => 0;

  void bump() => state++;
}

final isAdminWalletProvider = Provider<bool>((ref) {
  final addresses = AppConfig.instance.adminWalletAddresses;
  if (addresses.isEmpty) return false;

  final wallet = ref.watch(walletAuthStateProvider).walletAddress;
  if (wallet == null || wallet.isEmpty) return false;

  final normalized = wallet.toUpperCase();
  return addresses.any((a) => a.toUpperCase() == normalized);
});
