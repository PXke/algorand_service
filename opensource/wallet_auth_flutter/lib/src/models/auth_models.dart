import '../arc0060/arc0060.dart';
import '../caip122/caip122_message.dart';

/// Preferred login proof methods (server verifies per `proof_method`).
enum AuthProofMethod {
  arc0060('arc0060'),
  arc0025Txn('arc0025_txn'),
  legacyMessage('legacy_message'),

  /// algosdk signBytes convention (signature over b"MX" + message) — what
  /// Pera's `algo_signData` produces.
  signedBytes('signed_bytes');

  const AuthProofMethod(this.apiValue);
  final String apiValue;
}

class AuthNonce {
  const AuthNonce({
    required this.walletAddress,
    required this.nonce,
    required this.signingMessage,
    required this.caip122,
    required this.expiresInSeconds,
  });

  factory AuthNonce.fromJson(Map<String, dynamic> json) {
    return AuthNonce(
      walletAddress: json['wallet_address'] as String,
      nonce: json['nonce'] as String,
      signingMessage: json['signing_message'] as String,
      caip122: Caip122Message.fromJson(json['caip122'] as Map<String, dynamic>),
      expiresInSeconds: json['expires_in_seconds'] as int,
    );
  }

  final String walletAddress;
  final String nonce;
  final String signingMessage;
  final Caip122Message caip122;
  final int expiresInSeconds;
}

class AuthSession {
  const AuthSession({
    required this.sessionToken,
    required this.walletAddress,
    required this.expiresInEpoch,
    this.proofMethod,
  });

  factory AuthSession.fromJson(Map<String, dynamic> json) {
    return AuthSession(
      sessionToken: json['session_token'] as String,
      walletAddress: json['wallet_address'] as String,
      expiresInEpoch: json['expires_in_epoch'] as int,
      proofMethod: json['proof_method'] as String?,
    );
  }

  final String sessionToken;
  final String walletAddress;
  final int expiresInEpoch;
  final String? proofMethod;
}

class WalletConnection {
  const WalletConnection({
    required this.walletAddress,
    required this.connectorId,
  });

  final String walletAddress;
  final String connectorId;
}

/// Wallet-produced login proof returned to the backend.
class WalletAuthProof {
  const WalletAuthProof._(
    this.method, {
    this.signedTxnBase64,
    this.arc0060,
    this.signatureBase64,
  });

  factory WalletAuthProof.arc0060(Arc0060Proof proof) =>
      WalletAuthProof._(AuthProofMethod.arc0060, arc0060: proof);

  factory WalletAuthProof.arc0025Txn(String signedTxnBase64) =>
      WalletAuthProof._(AuthProofMethod.arc0025Txn, signedTxnBase64: signedTxnBase64);

  factory WalletAuthProof.signedBytes(String signatureBase64) =>
      WalletAuthProof._(AuthProofMethod.signedBytes, signatureBase64: signatureBase64);

  final AuthProofMethod method;
  final String? signedTxnBase64;
  final Arc0060Proof? arc0060;
  final String? signatureBase64;
}
