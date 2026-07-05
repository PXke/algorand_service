import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/auth_api.dart';
import '../config/wallet_auth_config.dart';
import '../storage/session_storage.dart';
import '../wallet/wallet_connector.dart';
import 'wallet_auth_state.dart';

/// Framework-agnostic Algorand wallet authentication orchestrator.
///
/// Flow: WalletConnect (ARC-0025) → ARC-0060 `algo_signData` when supported,
/// else ARC-0025 `algo_signTxn` with SIWA message in the txn note.
class WalletAuthClient {
  WalletAuthClient({
    required WalletAuthConfig config,
    required AuthApi authApi,
    required WalletConnector walletConnector,
    required SessionStorage sessionStorage,
  })  : _config = config,
        _authApi = authApi,
        _wallet = walletConnector,
        _sessionStorage = sessionStorage;

  final WalletAuthConfig _config;
  final AuthApi _authApi;
  final WalletConnector _wallet;
  final SessionStorage _sessionStorage;

  final ValueNotifier<WalletAuthState> state =
      ValueNotifier(const WalletAuthState());

  WalletConnector get walletConnector => _wallet;
  AuthApi get authApi => _authApi;

  Future<void> restoreSession() async {
    final token = await _sessionStorage.read(_config.sessionStorageKey);
    if (token == null) return;

    try {
      final json = await _authApi.getSession(token);
      state.value = WalletAuthState(
        sessionToken: token,
        walletAddress: json['wallet_address'] as String?,
      );
    } catch (_) {
      await _sessionStorage.delete(_config.sessionStorageKey);
    }
  }

  bool _cancelRequested = false;

  Future<void> connectAndSignIn({void Function(String wcUri)? onDisplayUri}) async {
    _cancelRequested = false;
    state.value = state.value.copyWith(isLoading: true, clearError: true);
    try {
      final connection = await _wallet
          .connect(onDisplayUri: onDisplayUri)
          .timeout(const Duration(minutes: 3));
      final nonce = await _authApi.requestNonce(connection.walletAddress);

      // Without a timeout an ignored/never-answered sign request leaves the
      // client loading forever — the wallet side has no failure signal.
      final proof = await _wallet
          .signLoginProof(
            walletAddress: connection.walletAddress,
            nonce: nonce,
          )
          .timeout(const Duration(minutes: 3));

      final session = await _authApi.verifyLogin(
        walletAddress: connection.walletAddress,
        nonce: nonce.nonce,
        proof: proof,
      );

      await _sessionStorage.write(_config.sessionStorageKey, session.sessionToken);
      state.value = WalletAuthState(
        walletAddress: session.walletAddress,
        sessionToken: session.sessionToken,
        isLoading: false,
      );
    } catch (e, st) {
      if (kDebugMode) {
        debugPrint('WalletAuthClient.connectAndSignIn failed: $e\n$st');
      }
      // Tear down any half-open pairing: a paired-but-unsigned session would
      // make the next createSession throw, so retry must start clean.
      try {
        await _wallet.disconnect();
      } catch (_) {}
      state.value = _cancelRequested
          ? state.value.copyWith(isLoading: false, clearError: true)
          : state.value.copyWith(isLoading: false, error: e);
    }
  }

  /// Abort an in-flight WalletConnect session (e.g. user closed the pairing
  /// dialog). The abort makes the pending connect/sign future throw; the
  /// [_cancelRequested] flag keeps that expected failure out of [state].error.
  Future<void> cancelPendingConnect() async {
    _cancelRequested = true;
    try {
      await _wallet.disconnect();
    } catch (_) {}
    state.value = state.value.copyWith(isLoading: false, clearError: true);
  }

  Future<void> logout() async {
    final token = state.value.sessionToken;
    if (token != null) {
      try {
        await _authApi.logout(token);
      } catch (_) {}
    }
    await _wallet.disconnect();
    await _sessionStorage.delete(_config.sessionStorageKey);
    state.value = const WalletAuthState();
  }

  void dispose() {
    state.dispose();
  }
}
