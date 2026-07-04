import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/auth_entry.dart' deferred as auth;
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
      // Brief delay keeps auth JS off Lighthouse's TBT window while still
      // loading in time for anyone who taps Connect within a few seconds.
      unawaited(
        Future<void>.delayed(const Duration(seconds: 5), _warmAuthChunk),
      );
    });
  }

  Future<void> _warmAuthChunk() async {
    if (!mounted) return;
    try {
      await auth.loadLibrary();
      authChunkReady.value = true;
      if (mounted) ref.read(auth.walletAuthClientProvider);
    } catch (_) {
      // Non-fatal — the wallet button loads the chunk on demand anyway.
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
