import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:go_router/go_router.dart';

import '../api/api_client.dart';

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
  bool _sawInitialRoute = false;
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
    if (!_sawInitialRoute) {
      // The first push is the initial page load, already recorded
      // server-side by the SSR document route that served this session.
      _sawInitialRoute = true;
      _lastPath = path;
      return;
    }
    if (path == _lastPath) return; // rebuild/redirect settling, not a real move
    _lastPath = path;
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
