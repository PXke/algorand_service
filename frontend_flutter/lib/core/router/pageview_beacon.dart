import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:go_router/go_router.dart';

import '../api/api_client.dart';
import '../util/ssr_pageview_track_stub.dart'
    if (dart.library.html) '../util/ssr_pageview_track_web.dart' as ssr_track;

/// Reports Flutter in-app route changes (client-side navigation between
/// pages) to the backend beacon endpoint. The SSR document routes only see
/// the very first request of a visit — everything after that is a client-side
/// GoRouter transition that never touches the server, so without this the
/// admin dashboard would systematically undercount page depth for engaged
/// readers. See backend/app/modules/seo/analytics_store.py and
/// backend/app/modules/seo/api/routes.py:beacon_pageview.
class PageviewBeaconObserver extends NavigatorObserver {
  PageviewBeaconObserver({ApiClient? client}) : _client = client ?? ApiClient();

  final ApiClient _client;
  GoRouter? _router;
  String? _lastPath;

  /// Wired up right after the GoRouter is constructed (a `GoRouter` can't
  /// reference itself while still building its own `observers` list).
  void attachRouter(GoRouter router) {
    _router = router;
  }

  @override
  void didPush(Route<dynamic> route, Route<dynamic>? previousRoute) => _report();

  @override
  void didReplace({Route<dynamic>? newRoute, Route<dynamic>? oldRoute}) => _report();

  void _report() {
    final router = _router;
    if (router == null) return;
    final path = router.routerDelegate.currentConfiguration.uri.path;
    if (path == _lastPath) return; // rebuild/redirect settling, not a real move
    _lastPath = path;
    // SSR document routes stamp sessionStorage; skip the duplicate beacon for
    // the landing page the server already counted.
    if (ssr_track.consumeSsrTrackedPath(path)) return;
    unawaited(_send(path));
  }

  Future<void> _send(String path) async {
    try {
      await _client.postJson('/api/v1/analytics/pageview', body: {'path': path});
    } catch (_) {
      // Best-effort beacon; a network hiccup must never affect navigation.
    }
  }
}
