import '../arc0025/arc0025_uri.dart';

/// Configuration for [WalletAuthClient] and wallet connectors.
class WalletAuthConfig {
  const WalletAuthConfig({
    required this.apiBaseUrl,
    this.algodApiUrl = 'https://testnet-api.algonode.cloud',
    // Pera's live WC v1 bridge; the official bridge.walletconnect.org was
    // shut down in 2023 (Pera mirrors a..h, see wc.perawallet.app/config.json).
    this.walletConnectBridge = 'https://wallet-connect-a.perawallet.app',
    this.walletConnectChainId = AlgorandWalletConnectChainId.testnet,
    this.dappName = 'Algorand dApp',
    this.dappDescription = 'Algorand application',
    this.dappUrl = 'https://algorand.com',
    this.dappIcons = const [],
    this.sessionStorageKey = 'wallet_auth_session_token',
    this.signInPrompt = 'Sign in with your Algorand wallet',
    this.enableArc0060 = false,
  });

  final String apiBaseUrl;
  final String algodApiUrl;
  final String walletConnectBridge;

  /// ARC-0025 WalletConnect chain id (416002 = TestNet, 416001 = MainNet).
  final int walletConnectChainId;
  final String dappName;
  final String dappDescription;
  final String dappUrl;
  final List<String> dappIcons;
  final String sessionStorageKey;
  final String signInPrompt;

  /// Try ARC-0060 `algo_signData` before the ARC-0025 0-ALGO txn fallback.
  /// Off by default: Pera over WalletConnect v1 renders the request as a
  /// confusing "Unsigned Request" with raw data blobs and answers in its own
  /// arbitrary-data format that is not an ARC-0060 response, so the attempt
  /// can only ever add an extra wallet prompt.
  final bool enableArc0060;
}
