import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'auth_entry.dart' deferred as auth;

/// Single deferred-import site for the auth chunk. Multiple `deferred as auth`
/// imports of [auth_entry] duplicate the WalletConnect payload in separate
/// `.part.js` files; everything that needs auth goes through this gate instead.
Future<void>? _library;

Future<void> loadAuthModule() => _library ??= auth.loadLibrary();

Widget buildWalletAppBarAction() => auth.WalletAppBarAction();

/// Touch the auth Riverpod graph so session restore can start after the chunk
/// is loaded (native warm path / post-tap).
void warmAuthProviders(WidgetRef ref) {
  ref.read(auth.walletAuthClientProvider);
}
