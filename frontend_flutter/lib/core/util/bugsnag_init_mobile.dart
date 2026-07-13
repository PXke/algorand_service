import 'dart:ui' show PlatformDispatcher;

import 'package:bugsnag_flutter/bugsnag_flutter.dart';
import 'package:flutter/material.dart';

import 'bugsnag_config.dart';

Future<void> initBugsnag() async {
  if (!BugsnagConfig.enabled) return;
  try {
    await bugsnag.start(
      apiKey: BugsnagConfig.apiKey,
      releaseStage: BugsnagConfig.releaseStage,
    );
    final priorFlutter = FlutterError.onError;
    FlutterError.onError = (details) {
      bugsnag.notify(details.exception, details.stack ?? StackTrace.current);
      priorFlutter?.call(details);
    };

    final priorPlatform = PlatformDispatcher.instance.onError;
    PlatformDispatcher.instance.onError = (error, stack) {
      bugsnag.notify(error, stack);
      return priorPlatform?.call(error, stack) ?? false;
    };
  } catch (_) {
    // Never let crash reporting setup block app start.
  }
}
