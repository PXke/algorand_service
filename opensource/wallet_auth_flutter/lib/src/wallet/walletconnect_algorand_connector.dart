import 'dart:convert';

import 'package:algorand_dart/algorand_dart.dart';
import 'package:ensemble_walletconnect/ensemble_walletconnect.dart';
import 'package:flutter/foundation.dart';

import '../arc0025/arc0025_uri.dart';
import '../arc0060/arc0060.dart';
import '../config/wallet_auth_config.dart';
import '../models/auth_models.dart';
import 'wallet_connector.dart';

/// WalletConnect connector implementing ARC-0025 (`algo_signTxn`) and ARC-0060 (`algo_signData`).
class WalletConnectAlgorandConnector implements WalletConnector {
  WalletConnectAlgorandConnector({required WalletAuthConfig config}) : _config = config {
    _algorand = Algorand(algodClient: AlgodClient(apiUrl: _config.algodApiUrl));
  }

  final WalletAuthConfig _config;
  WalletConnect? _connectorInstance;
  late final Algorand _algorand;

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

    final sender = Address.fromAlgorandAddress(address: walletAddress);
    final params = await _algorand.getSuggestedTransactionParams();

    final tx = await (PaymentTransactionBuilder()
          ..sender = sender
          ..receiver = sender
          ..amount = Algo.toMicroAlgos(0)
          ..noteText = signingMessage
          ..suggestedParams = params)
        .build();
    // This proof is verified by the backend and never broadcast. A 0 fee is
    // below the network minimum, so the signed blob is unsubmittable even if
    // leaked — and the wallet shows the login as costing nothing.
    tx.fee = 0;

    final txBytes = tx.toBytes();
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
