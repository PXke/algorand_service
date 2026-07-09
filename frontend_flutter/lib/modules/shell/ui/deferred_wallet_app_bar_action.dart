import 'package:flutter/material.dart';

import '../../../core/deferred/deferred_load_pool.dart';
import '../auth_chunk_ready.dart';
import '../../auth/auth_deferred_gate.dart';

/// Wallet connect control — loads the auth chunk only when tapped (or when
/// [AuthChunkPreloader] warms it in the background for returning visitors).
class DeferredWalletAppBarAction extends StatefulWidget {
  const DeferredWalletAppBarAction({super.key});

  @override
  State<DeferredWalletAppBarAction> createState() =>
      _DeferredWalletAppBarActionState();
}

class _DeferredWalletAppBarActionState extends State<DeferredWalletAppBarAction> {
  Future<void>? _library;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    if (authChunkReady.value) {
      _load();
    } else {
      authChunkReady.addListener(_onChunkReady);
    }
  }

  @override
  void dispose() {
    authChunkReady.removeListener(_onChunkReady);
    super.dispose();
  }

  void _onChunkReady() {
    if (authChunkReady.value && mounted && !_ready) {
      _load();
    }
  }

  Future<void> _load() async {
    // One auth deferred-import site ([loadAuthModule]); still serialize so we
    // do not race other chunks on the global queue.
    _library ??= serializeDeferredLoad(loadAuthModule);
    await _library;
    authChunkReady.value = true;
    if (mounted) setState(() => _ready = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_ready) {
      return buildWalletAppBarAction();
    }
    return IconButton(
      tooltip: 'Wallet',
      onPressed: _load,
      icon: const Icon(Icons.account_balance_wallet_outlined, size: 20),
    );
  }
}
