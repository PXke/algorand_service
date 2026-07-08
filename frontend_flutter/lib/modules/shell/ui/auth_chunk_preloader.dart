import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/auth_entry.dart' deferred as auth;
import '../../../core/deferred/deferred_load_pool.dart';
import '../auth_chunk_ready.dart';

/// Loads the wallet-auth deferred chunk after first paint so returning visitors
/// get session restore without blocking initial JS evaluation.
class AuthChunkPreloader extends ConsumerStatefulWidget {
  const AuthChunkPreloader({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<AuthChunkPreloader> createState() => _AuthChunkPreloaderState();
}

class _AuthChunkPreloaderState extends ConsumerState<AuthChunkPreloader> {
  @override
  void initState() {
    super.initState();
    // On web, concurrent deferred chunk downloads race dart2js's multi-loader
    // (DeferredLoadException: Success callback…). Wallet loads on demand instead.
    if (kIsWeb) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(
        Future<void>.delayed(const Duration(seconds: 5), _warmAuthChunk),
      );
    });
  }

  Future<void> _warmAuthChunk() async {
    if (!mounted) return;
    try {
      await serializeDeferredLoad(() => auth.loadLibrary());
      authChunkReady.value = true;
      if (mounted) ref.read(auth.walletAuthClientProvider);
    } catch (_) {
      // Non-fatal — the wallet button loads the chunk on demand anyway.
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
