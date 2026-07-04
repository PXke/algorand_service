import 'package:bugsnag_flutter/bugsnag_flutter.dart';
import 'package:flutter/material.dart';

Future<void> initBugsnag() async {
  try {
    await bugsnag.start(apiKey: '7712dd9a5b49cc654fd24ce23a18d0c3');
    final priorOnError = FlutterError.onError;
    FlutterError.onError = (details) {
      bugsnag.notify(details.exception, details.stack ?? StackTrace.current);
      priorOnError?.call(details);
    };
  } catch (_) {
    // Never let crash reporting setup block app start.
  }
}
