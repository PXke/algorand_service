import '../models/auth_models.dart';

/// Backend auth API contract (implement for your server or mocks).
abstract class AuthApi {
  Future<AuthNonce> requestNonce(String walletAddress);

  Future<AuthSession> verifyLogin({
    required String walletAddress,
    required String nonce,
    required WalletAuthProof proof,
  });

  @Deprecated('Use verifyLogin with WalletAuthProof.arc0025Txn')
  Future<AuthSession> verifyWithSignedTransaction({
    required String walletAddress,
    required String nonce,
    required String signedTxnBase64,
  });

  @Deprecated('Use verifyLogin with legacy proof when needed')
  Future<AuthSession> verifyWithMessageSignature({
    required String walletAddress,
    required String nonce,
    required String signatureBase64,
  });

  Future<Map<String, dynamic>> getSession(String sessionToken);

  Future<void> logout(String sessionToken);
}
