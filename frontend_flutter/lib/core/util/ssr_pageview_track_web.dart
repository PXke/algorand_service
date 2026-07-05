// ignore_for_file: deprecated_member_use, avoid_web_libraries_in_flutter
import 'dart:html' as html;

const _key = 'pxke_ssr_pv';

/// True when [path] was already counted by the SSR document route for this tab.
bool consumeSsrTrackedPath(String path) {
  try {
    final storage = html.window.sessionStorage;
    final tracked = storage[_key];
    if (tracked == path) {
      storage.remove(_key);
      return true;
    }
  } catch (_) {
    // sessionStorage blocked — rely on server-side dedup instead.
  }
  return false;
}
