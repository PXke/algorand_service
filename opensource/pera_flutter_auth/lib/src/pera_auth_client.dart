import 'auth_models.dart';
import 'wallet_provider.dart';

class PeraAuthClient {
  PeraAuthClient({required WalletProvider walletProvider})
      : _walletProvider = walletProvider;

  final WalletProvider _walletProvider;

  Future<WalletSession> connectWallet() => _walletProvider.connect();

  Future<SignedNonce> signNonce(String nonceMessage) {
    return _walletProvider.signNonce(nonceMessage);
  }

  Future<void> disconnectWallet() => _walletProvider.disconnect();
}
