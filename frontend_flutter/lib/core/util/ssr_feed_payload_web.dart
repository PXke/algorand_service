import 'dart:convert';

import 'package:web/web.dart' as web;

/// Feed rows embedded by the backend SSR home route (`#pxke-ssr-feed`).
/// Uses `package:web` so this works on both dart2js and dart2wasm builds.
List<Map<String, dynamic>>? readSsrFeedItems() {
  final el = web.document.getElementById('pxke-ssr-feed');
  if (el == null) return null;
  final text = (el.textContent ?? '').trim();
  if (text.isEmpty) return null;
  try {
    final body = jsonDecode(text) as Map<String, dynamic>;
    final raw = body['items'];
    if (raw is! List) return null;
    // Drop the script so Ctrl+F does not match invisible JSON duplicates.
    el.remove();
    return raw.map((e) => Map<String, dynamic>.from(e as Map)).toList();
  } catch (_) {
    return null;
  }
}
