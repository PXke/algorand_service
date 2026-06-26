/// Wallet-agnostic Algorand authentication for Flutter.
///
/// Supports ARC-0025 (WalletConnect), ARC-0060 (arbitrary AUTH signing), and
/// SIWA / CAIP-122 login messages. No Riverpod/Bloc dependency — wrap
/// [WalletAuthClient] in your state layer.
library wallet_auth_flutter;

export 'src/api/auth_api.dart';
export 'src/api/http_auth_api.dart';
export 'src/arc0025/arc0025_uri.dart';
export 'src/arc0060/arc0060.dart';
export 'src/caip122/caip122_message.dart';
export 'src/client/wallet_auth_client.dart';
export 'src/client/wallet_auth_state.dart';
export 'src/config/wallet_auth_config.dart';
export 'src/models/auth_models.dart';
export 'src/siwa/siwa_message.dart';
export 'src/storage/memory_session_storage.dart';
export 'src/storage/secure_session_storage.dart';
export 'src/storage/session_storage.dart';
export 'src/wallet/wallet_connector.dart';
export 'src/wallet/walletconnect_algorand_connector.dart';
