/// Runtime configuration for the Flutter client.
///
/// Override at build/run time:
///   flutter run -d chrome \
///     --dart-define=API_BASE_URL=http://127.0.0.1:8080 \
///     --dart-define=AUTH_DOMAIN=localhost
class AppConfig {
  const AppConfig({
    required this.apiBaseUrl,
    required this.authDomain,
    required this.algodApiUrl,
    required this.walletConnectBridge,
    required this.walletConnectChainId,
    required this.explorerBaseUrl,
    required this.adminWalletAddresses,
    required this.suggestionsEnabled,
  });

  final String apiBaseUrl;
  final String authDomain;
  final String algodApiUrl;
  final String walletConnectBridge;
  final int walletConnectChainId;
  final String explorerBaseUrl;
  /// Comma-separated Algorand addresses allowed to open the admin panel.
  final List<String> adminWalletAddresses;

  /// Community suggestions board (wallet-gated submit/upvote). Off by default.
  final bool suggestionsEnabled;

  String explorerTxUrl(String txid) => '$explorerBaseUrl/tx/$txid';

  static AppConfig fromEnvironment() {
    return AppConfig(
      apiBaseUrl: const String.fromEnvironment(
        'API_BASE_URL',
        defaultValue: 'http://127.0.0.1:8080',
      ),
      authDomain: const String.fromEnvironment(
        'AUTH_DOMAIN',
        defaultValue: 'localhost',
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
      explorerBaseUrl: const String.fromEnvironment(
        'EXPLORER_BASE_URL',
        defaultValue: 'https://testnet.explorer.perawallet.app',
      ),
      adminWalletAddresses: _parseAddressList(
        const String.fromEnvironment('ADMIN_WALLET_ADDRESSES', defaultValue: ''),
      ),
      suggestionsEnabled: const String.fromEnvironment(
            'SUGGESTIONS_ENABLED',
            defaultValue: 'false',
          ) ==
          'true',
    );
  }

  static List<String> _parseAddressList(String raw) {
    if (raw.trim().isEmpty) return const [];
    return raw
        .split(',')
        .map((s) => s.trim())
        .where((s) => s.isNotEmpty)
        .toList();
  }

  static final AppConfig instance = AppConfig.fromEnvironment();
}

/// Route an external image through the same-origin image proxy. Flutter web
/// (CanvasKit) renders Image.network by fetching via XHR, which needs CORS
/// headers most external hosts omit — so external article images fail without
/// this. Relative / data / already-same-origin URLs pass through unchanged.
String proxiedImageUrl(String url) {
  if (url.isEmpty || !url.startsWith('http')) return url;
  final base = AppConfig.instance.apiBaseUrl;
  if (base.isEmpty || url.startsWith(base)) return url;
  return '$base/api/v1/img?url=${Uri.encodeComponent(url)}';
}

/// Per-domain favicon for an article's source URL, routed through the image proxy
/// (which caches it in Redis 24h and adds the CORS header CanvasKit needs). Uses
/// DuckDuckGo's icon service server-side — reliable, returns a fallback glyph, and
/// the browser never hits a third party. Returns null when there's no usable host.
String? faviconUrl(String? sourceUrl) {
  if (sourceUrl == null || sourceUrl.isEmpty) return null;
  final host = Uri.tryParse(sourceUrl)?.host ?? '';
  if (host.isEmpty) return null;
  return proxiedImageUrl('https://icons.duckduckgo.com/ip3/$host.ico');
}

/// Curated, high-quality logos for frequent sources, keyed by bare host. These
/// win over the generic icon service so a brand (e.g. Pera Wallet) reads cleanly
/// at card size. Values may be absolute URLs OR same-origin asset paths we save
/// on our side (e.g. "/icons/sources/perawallet.png"). Extend as needed.
const Map<String, String> kSourceLogos = {};

/// Proxied logo for a bare host: a curated logo if we have one, else the icon
/// service (the site's touch icon — effectively its logo for app sites). The
/// proxy 404s on hosts the icon service doesn't know, so callers' image
/// errorBuilder falls back cleanly.
String _logoForHost(String host) {
  final curated = kSourceLogos[host];
  return proxiedImageUrl(curated ?? 'https://icons.duckduckgo.com/ip3/$host.ico');
}

/// A source's brand logo for use as a *card-image fallback* (bigger than the
/// inline [faviconUrl] chip). Returns null when there's no usable host.
String? sourceLogoUrl(String? sourceUrl) {
  if (sourceUrl == null || sourceUrl.isEmpty) return null;
  final host = (Uri.tryParse(sourceUrl)?.host ?? '').replaceFirst('www.', '');
  if (host.isEmpty) return null;
  return _logoForHost(host);
}

/// A logo derived from a `service_id` slug. Stories rarely carry a source_url,
/// but the service_id is the source domain with dots slugified to dashes
/// (e.g. "perawallet-app" -> "perawallet.app"). Reconstruct it; synthetic ids
/// (e.g. "weekly-digest") resolve to a host the icon service 404s on, so the
/// caller's errorBuilder falls back to the monogram — no bogus globe icons.
String? serviceLogoUrl(String? serviceId) {
  if (serviceId == null) return null;
  final s = serviceId.trim().toLowerCase();
  if (!s.contains('-') || !RegExp(r'^[a-z0-9.-]+$').hasMatch(s)) return null;
  return _logoForHost(s.replaceAll('-', '.'));
}

/// Best logo for a story tile: the source URL's host when present, otherwise the
/// service_id-derived host. Null when neither yields a usable host.
String? articleLogoUrl({String? sourceUrl, String? serviceId}) =>
    sourceLogoUrl(sourceUrl) ?? serviceLogoUrl(serviceId);
