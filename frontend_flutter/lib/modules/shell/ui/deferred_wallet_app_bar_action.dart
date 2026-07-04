import 'package:flutter/material.dart';

import '../auth_chunk_ready.dart';
import '../../auth/auth_entry.dart' deferred as auth;

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
      _ready = true;
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
      setState(() => _ready = true);
    }
  }

  Future<void> _load() async {
    _library ??= auth.loadLibrary();
    await _library;
    authChunkReady.value = true;
    if (mounted) setState(() => _ready = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_ready) {
      return auth.WalletAppBarAction();
    }
    return IconButton(
      tooltip: 'Wallet',
      onPressed: _load,
      icon: const Icon(Icons.account_balance_wallet_outlined, size: 20),
    );
  }
}
