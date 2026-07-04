import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_web_plugins/url_strategy.dart';

import 'app.dart';
import 'core/util/bugsnag_init_stub.dart'
    if (dart.library.io) 'core/util/bugsnag_init_mobile.dart';

Future<void> main() async {
  // Path URLs (no /#/): the default HASH strategy made the engine read the
  // initial route from the empty fragment on SSR-served deep links
  // (/news/articles/{id}), so go_router fell back to '/' and the app snapped
  // to the front page the moment the canvas loaded. Must run before the
  // router is created.
  usePathUrlStrategy();
  WidgetsFlutterBinding.ensureInitialized();
  runApp(ProviderScope(child: AlgorandPlatformApp()));
  // Crash reporting is mobile-only; web skips it entirely for a leaner bundle.
  unawaited(initBugsnag());
}
