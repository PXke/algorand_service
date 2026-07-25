import 'package:web/web.dart' as web;

/// Firefox for Android does not reliably hand off custom URI schemes
/// (`wc:...`, `perawallet-wc://...`) passed through
/// `window.open(url, '_self', 'noopener,noreferrer')` — that path doesn't
/// consistently go through Gecko's external-protocol/intent resolver the way
/// a genuine top-level navigation does (confirmed against Mozilla bug
/// reports, 2026-07-21). Assigning `location.href` directly is the form
/// every mobile browser (Chrome, Safari, Firefox) reliably routes through
/// its protocol handler / Android intent resolver.
bool navigateCurrentWindow(String url) {
  web.window.location.href = url;
  return true;
}
