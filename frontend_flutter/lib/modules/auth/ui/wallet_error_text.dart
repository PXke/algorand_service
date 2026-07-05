import 'dart:async';

import '../../../core/l10n/l10n_extensions.dart';

/// Maps a [WalletAuthState.error] object to a message a reader can act on.
///
/// The underlying errors are a grab bag (WalletConnect exceptions, HTTP
/// failures, [TimeoutException]) with no shared type, so this keys off the
/// class where possible and the message text otherwise.
String walletErrorText(AppLocalizations l10n, Object error) {
  if (error is TimeoutException) return l10n.walletErrorTimeout;
  final text = error.toString().toLowerCase();
  if (text.contains('reject') ||
      text.contains('denied') ||
      text.contains('declined') ||
      text.contains('cancel')) {
    return l10n.walletErrorRejected;
  }
  return l10n.walletErrorGeneric;
}
