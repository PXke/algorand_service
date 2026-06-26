import 'package:wallet_auth_flutter/wallet_auth_flutter.dart';

import '../../core/config/app_config.dart';

String dappUrlFromAuthDomain(String authDomain) {
  if (authDomain.startsWith('http')) return authDomain;
  if (authDomain.contains(':')) return 'http://$authDomain';
  return 'https://$authDomain';
}

/// HTTPS URL for the dApp icon sent in WalletConnect session metadata.
///
/// Wallets (e.g. Pera) fetch this over the network and show an empty square
/// when [WalletAuthConfig.dappIcons] is omitted.
String dappIconUrlFromDappUrl(String dappUrl) {
  final base = dappUrl.endsWith('/') ? dappUrl.substring(0, dappUrl.length - 1) : dappUrl;
  return '$base/icons/Icon-192.png';
}

WalletAuthConfig walletAuthConfigFromApp(AppConfig app) {
  final dappUrl = dappUrlFromAuthDomain(app.authDomain);
  return WalletAuthConfig(
    apiBaseUrl: app.apiBaseUrl,
    algodApiUrl: app.algodApiUrl,
    walletConnectBridge: app.walletConnectBridge,
    walletConnectChainId: app.walletConnectChainId,
    dappName: 'PXke Algorand',
    dappDescription: 'Independent coverage of the Algorand ecosystem',
    dappUrl: dappUrl,
    dappIcons: [dappIconUrlFromDappUrl(dappUrl)],
  );
}

WalletAuthClient createWalletAuthClient(AppConfig app) {
  final config = walletAuthConfigFromApp(app);
  return WalletAuthClient(
    config: config,
    authApi: HttpAuthApi(config: config),
    walletConnector: WalletConnectAlgorandConnector(config: config),
    sessionStorage: SecureSessionStorage(),
  );
}
