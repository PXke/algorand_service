import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/deferred/deferred_load_pool.dart';
import '../../auth/auth_deferred_gate.dart';
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
    WidgetsBinding.instance.addPostFrameCallback((_) {
      // Web: longer delay so locale / markets / newspaper deferred downloads
      // finish before we warm auth (dart2js multi-loader races). Native: shorter.
      final delay = kIsWeb
          ? const Duration(seconds: 8)
          : const Duration(seconds: 5);
      unawaited(Future<void>.delayed(delay, _warmAuthChunk));
    });
  }

  Future<void> _warmAuthChunk() async {
    if (!mounted || authChunkReady.value) return;
    try {
      await serializeDeferredLoad(loadAuthModule);
      authChunkReady.value = true;
      if (mounted) warmAuthProviders(ref);
    } catch (_) {
      // Non-fatal — the wallet button loads the chunk on demand anyway.
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
