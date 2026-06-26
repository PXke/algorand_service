import 'package:bugsnag_flutter/bugsnag_flutter.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await bugsnag.start(apiKey: '7712dd9a5b49cc654fd24ce23a18d0c3');
    // Route uncaught Flutter framework errors to Bugsnag.
    final priorOnError = FlutterError.onError;
    FlutterError.onError = (details) {
      bugsnag.notify(details.exception, details.stack ?? StackTrace.current);
      priorOnError?.call(details);
    };
  } catch (_) {
    // Never let crash reporting setup block app start.
  }
  runApp(ProviderScope(child: AlgorandPlatformApp()));
}
