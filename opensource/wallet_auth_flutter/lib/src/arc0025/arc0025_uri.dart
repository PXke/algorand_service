/// ARC-0025 WalletConnect session URI helpers.
String withAlgorandWalletConnectParam(String wcUri) {
  final uri = Uri.parse(wcUri);
  if (uri.queryParameters['algorand'] == 'true') {
    return wcUri;
  }
  return uri.replace(queryParameters: {...uri.queryParameters, 'algorand': 'true'}).toString();
}

/// ARC-0025 chain IDs (see ARC-0025).
abstract final class AlgorandWalletConnectChainId {
  static const int legacyAll = 4160;
  static const int mainnet = 416001;
  static const int testnet = 416002;
  static const int betanet = 416003;
}
