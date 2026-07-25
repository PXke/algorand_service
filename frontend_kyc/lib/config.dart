/// Runtime configuration for the KYC app. Deliberately its own project
/// (separate from the news website's Flutter app) — different audience
/// (third-party integrators, not readers), different brand, own deploy.
///
/// Override at build/run time:
///   flutter run -d chrome \
///     --dart-define=API_BASE_URL=http://127.0.0.1:8080
class AppConfig {
  const AppConfig({
    required this.apiBaseUrl,
    required this.algodApiUrl,
    required this.walletConnectBridge,
    required this.walletConnectChainId,
  });

  final String apiBaseUrl;
  final String algodApiUrl;
  final String walletConnectBridge;

  /// ARC-0025 WalletConnect chain id (416002 = TestNet, 416001 = MainNet).
  final int walletConnectChainId;

  static AppConfig fromEnvironment() {
    return AppConfig(
      apiBaseUrl: const String.fromEnvironment(
        'API_BASE_URL',
        defaultValue: 'http://127.0.0.1:8080',
      ),
      algodApiUrl: const String.fromEnvironment(
        'ALGOD_API_URL',
        defaultValue: 'https://testnet-api.algonode.cloud',
      ),
      // Pera keeps WalletConnect v1 alive on its own bridges (a..h); the
      // official bridge.walletconnect.org was shut down in 2023.
      walletConnectBridge: const String.fromEnvironment(
        'WALLET_CONNECT_BRIDGE',
        defaultValue: 'https://wallet-connect-a.perawallet.app',
      ),
      walletConnectChainId: int.tryParse(
            const String.fromEnvironment(
              'WALLET_CONNECT_CHAIN_ID',
              defaultValue: '416002',
            ),
          ) ??
          416002,
    );
  }

  static final AppConfig instance = AppConfig.fromEnvironment();
}
