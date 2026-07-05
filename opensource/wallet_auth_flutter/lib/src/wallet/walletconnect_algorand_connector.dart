import 'dart:convert';

import 'package:algorand_auth_core/algorand_auth_core.dart';
import 'package:ensemble_walletconnect/ensemble_walletconnect.dart';
import 'package:flutter/foundation.dart';

import '../arc0025/arc0025_uri.dart';
import '../arc0060/arc0060.dart';
import '../config/wallet_auth_config.dart';
import '../models/auth_models.dart';
import 'wallet_connector.dart';

/// WalletConnect connector implementing ARC-0025 (`algo_signTxn`) and ARC-0060 (`algo_signData`).
class WalletConnectAlgorandConnector implements WalletConnector {
  WalletConnectAlgorandConnector({required WalletAuthConfig config})
      : _config = config,
        _algod = AlgodClient(apiUrl: config.algodApiUrl);

  final WalletAuthConfig _config;
  final AlgodClient _algod;
  WalletConnect? _connectorInstance;

  /// Built LAZILY on first use: the WalletConnect constructor opens the bridge
  /// WebSocket immediately, so constructing it at app startup dialled the
  /// bridge (with retries) for every visitor — page-load console errors and
  /// wasted sockets for the vast majority who never log in.
  WalletConnect get _connector => _connectorInstance ??= WalletConnect(
        bridge: _config.walletConnectBridge,
        clientMeta: PeerMeta(
          name: _config.dappName,
          description: _config.dappDescription,
          url: _config.dappUrl,
          icons: _config.dappIcons,
        ),
      );

  SessionStatus? _session;

  @override
  String get id => 'walletconnect_algorand';

  @override
  String get displayName => 'WalletConnect (Algorand)';

  @override
  Future<WalletConnection> connect({void Function(String wcUri)? onDisplayUri}) async {
    _session = await _connector.createSession(
      chainId: _config.walletConnectChainId,
      onDisplayUri: (uri) {
        final patched = withAlgorandWalletConnectParam(uri);
        (onDisplayUri ?? (_) {})(patched);
      },
    );
    return WalletConnection(
      walletAddress: _session!.accounts.first,
      connectorId: id,
    );
  }

  @override
  Future<WalletAuthProof> signLoginProof({
    required String walletAddress,
    required AuthNonce nonce,
  }) async {
    if (_config.enableSignData) {
      final signature = await _trySignDataPera(walletAddress: walletAddress, nonce: nonce);
      if (signature != null) {
        return WalletAuthProof.signedBytes(signature);
      }
    }

    if (_config.enableArc0060) {
      final arc0060 = await _trySignArc0060(walletAddress: walletAddress, nonce: nonce);
      if (arc0060 != null) {
        return WalletAuthProof.arc0060(arc0060);
      }
    }

    final signedTxn = await _signArc0025AuthTxn(
      walletAddress: walletAddress,
      signingMessage: nonce.signingMessage,
    );
    return WalletAuthProof.arc0025Txn(signedTxn);
  }

  /// Pera-dialect arbitrary-data signing: one request item per datum, each
  /// carrying `{data: <b64>, message, signer, chainId}` (the shape
  /// @perawallet/connect v1.5.2 sends). Pera answers with an algosdk
  /// signBytes signature — ed25519 over b"MX" + data — as base64 or bytes.
  /// Returns null (falling back to the 0-ALGO txn) on any error, e.g. a
  /// wallet that does not implement the method.
  Future<String?> _trySignDataPera({
    required String walletAddress,
    required AuthNonce nonce,
  }) async {
    if (_session == null) return null;
    try {
      final result = await _connector.sendCustomRequest(
        method: 'algo_signData',
        params: [
          {
            'data': base64Encode(utf8.encode(nonce.signingMessage)),
            'message': _config.signInPrompt,
            'signer': walletAddress,
            'chainId': _config.walletConnectChainId,
          },
        ],
      );
      if (result is List && result.isNotEmpty) {
        final first = result.first;
        if (first is String && first.isNotEmpty) return first;
        if (first is List) return base64Encode(List<int>.from(first));
      }
      return null;
    } catch (e, st) {
      if (kDebugMode) {
        debugPrint('algo_signData failed, falling back to auth txn: $e\n$st');
      }
      return null;
    }
  }

  Future<Arc0060Proof?> _trySignArc0060({
    required String walletAddress,
    required AuthNonce nonce,
  }) async {
    if (_session == null) return null;

    final signRequest = buildArc0060SignRequest(
      caip122: nonce.caip122,
      walletAddress: walletAddress,
    );
    final metadata = {
      'scope': arc0060ScopeAuth,
      'encoding': arc0060EncodingBase64,
    };

    for (final method in const ['algo_signData', 'signData']) {
      try {
        final result = await _connector.sendCustomRequest(
          method: method,
          params: [signRequest, metadata],
        );
        final proof = parseArc0060SignResponse(
          result,
          domain: nonce.caip122.domain,
        );
        if (proof != null) return proof;
      } catch (e, st) {
        if (kDebugMode) {
          debugPrint('ARC-0060 $method failed: $e\n$st');
        }
      }
    }
    return null;
  }

  Future<String> _signArc0025AuthTxn({
    required String walletAddress,
    required String signingMessage,
  }) async {
    if (_session == null) {
      throw StateError('Wallet session is not connected');
    }

    final params = await _algod.suggestedParams();
    final txBytes = AuthPaymentTransaction.buildUnsignedBytes(
      senderAddress: walletAddress,
      note: signingMessage,
      params: params,
      feeMicroAlgos: 0,
    );

    final walletTxn = {
      'txn': base64Encode(txBytes),
      'signers': [walletAddress],
      'message': _config.signInPrompt,
    };

    final result = await _connector.sendCustomRequest(
      method: 'algo_signTxn',
      params: [
        [walletTxn],
        {'message': _config.signInPrompt},
      ],
    );

    if (result == null || result is! List || result.isEmpty) {
      throw StateError('Unable to sign auth transaction');
    }

    final first = result.first;
    if (first is String) {
      return first;
    }
    if (first is List<int>) {
      return base64Encode(first);
    }
    return base64Encode(List<int>.from(first as List));
  }

  @override
  Future<void> disconnect() async {
    // Never touch the lazy getter here — disconnecting when nothing was ever
    // connected must not open a bridge socket just to close it.
    final connector = _connectorInstance;
    if (connector != null && connector.connected) {
      await connector.killSession();
    }
    _session = null;
  }

  WalletConnect get rawConnector => _connector;
}
