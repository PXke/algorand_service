import 'package:algorand_platform/modules/auth/ui/wallet_connect_dialog.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  const wcUri =
      'wc:8a5d1c@1?bridge=https%3A%2F%2Fwallet-connect-a.perawallet.app&key=abc123&algorand=true';

  test('peraDeepLink wraps the WC uri in Pera\'s own scheme on iOS', () {
    // Bare `wc:` custom-scheme navigation from web content is unreliable on
    // iOS Safari — Pera's own web SDK never sends iOS the raw uri, it always
    // wraps it in `perawallet-wc://wc?uri=...` (perawallet/connect,
    // generatePeraWalletConnectDeepLink). This was the actual bug: every
    // platform used to get the Android-only bare-uri form.
    final link = peraDeepLink(wcUri, isIOS: true);
    expect(link, startsWith('perawallet-wc://wc?uri='));
    expect(link, contains(Uri.encodeComponent(wcUri)));
  });

  test('peraDeepLink leaves the WC uri untouched on Android', () {
    // Android's intent-filter resolution of arbitrary custom schemes is
    // reliable enough that Pera hands it the bare `wc:` uri unmodified.
    expect(peraDeepLink(wcUri, isIOS: false), wcUri);
  });

  test('peraDeepLink round-trips the truncated reopen-session uri', () {
    // _reopenWallet passes just the topic (no query string) to foreground an
    // already-paired session — the wrapping must work the same way for that
    // shorter form.
    const topicOnly = 'wc:8a5d1c@1';
    final link = peraDeepLink(topicOnly, isIOS: true);
    expect(link, 'perawallet-wc://wc?uri=${Uri.encodeComponent(topicOnly)}');
  });
}
