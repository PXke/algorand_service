import 'auth_models.dart';

abstract class WalletProvider {
  Future<WalletSession> connect();
  Future<void> disconnect();
  Future<SignedNonce> signNonce(String nonceMessage);
}
