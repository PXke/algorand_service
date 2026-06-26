class WalletSession {
  WalletSession({required this.walletAddress, required this.sessionTopic});

  final String walletAddress;
  final String sessionTopic;
}

class SignedNonce {
  SignedNonce({required this.nonce, required this.signatureBase64});

  final String nonce;
  final String signatureBase64;
}
