import 'package:algorand_platform/l10n/app_localizations.dart';
import 'package:algorand_platform/modules/auth/ui/wallet_connect_dialog.dart';
import 'package:flutter/material.dart';
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

  testWidgets(
      'app resume after a real backgrounding fires onResumed (dead-socket revival), '
      'a quick flicker does not', (tester) async {
    // Root cause of "login works on desktop, hangs on mobile" (2026-07-16):
    // deep-linking to Pera backgrounds the tab, the OS kills the bridge
    // WebSocket, and its reconnect budget (5 attempts) exhausts — so the
    // approval the wallet already sent never reaches the dapp. The dialog
    // must fire onResumed when the app foregrounds so the caller can
    // wakeTransport() and collect the queued response.
    //
    // But only after a real trip: onResumed also fires for the OS's own
    // "Open in Pera?" consent prompt flickering over the page for under a
    // second, and force-reconnecting on every one of those churns the
    // transport mid-request (2026-07-16 v1 regression) — so a sub-2s
    // backgrounding must NOT fire it.
    var resumed = 0;
    await tester.pumpWidget(
      MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showWalletConnectUriDialog(
              context,
              wcUri,
              onResumed: () => resumed++,
            ),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle(); // localization delegates load async
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    // Quick flicker: under the 2s threshold, must not fire. `DateTime.now()`
    // is real wall-clock time (not the test binding's fake frame clock), so
    // this needs an actual delay — real Timers only fire inside runAsync.
    await tester.runAsync(() async {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      await Future<void>.delayed(const Duration(milliseconds: 300));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    });
    await tester.pump();
    expect(resumed, 0);

    // A real trip to the wallet app and back: must fire.
    await tester.runAsync(() async {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      await Future<void>.delayed(const Duration(seconds: 2, milliseconds: 200));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    });
    await tester.pump();
    expect(resumed, 1);

    // Dismissing the dialog detaches the observer — no leak, no late calls.
    Navigator.of(tester.element(find.byType(AlertDialog))).pop();
    await tester.pumpAndSettle();
    await tester.runAsync(() async {
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
      await Future<void>.delayed(const Duration(seconds: 2, milliseconds: 200));
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    });
    await tester.pump();
    expect(resumed, 1);
  }, timeout: const Timeout(Duration(seconds: 30)));
}
