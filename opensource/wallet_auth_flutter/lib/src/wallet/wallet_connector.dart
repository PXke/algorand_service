import '../models/auth_models.dart';

/// Pluggable wallet transport (WalletConnect ARC-0025 / ARC-0060, hardware, etc.).
abstract class WalletConnector {
  String get id;
  String get displayName;

  Future<WalletConnection> connect({void Function(String wcUri)? onDisplayUri});

  /// Sign login using ARC-0060 when supported, otherwise ARC-0025 (`algo_signTxn`).
  Future<WalletAuthProof> signLoginProof({
    required String walletAddress,
    required AuthNonce nonce,
  });

  Future<void> disconnect();
}
