import 'dart:async';
import 'dart:js_interop';

import 'package:flutter/foundation.dart';
import 'package:web/web.dart' as web;

import 'bugsnag_config.dart';

const _bugsnagCdn = 'https://d2wy8f7a9ursnm.cloudfront.net/v8/bugsnag.min.js';

@JS('Bugsnag')
external BugsnagJs get _bugsnag;

@JS()
extension type BugsnagJs._(JSObject _) {
  external BugsnagClientJs start(JSObject options);
}

@JS()
extension type BugsnagClientJs._(JSObject _) {
  external void notify(JsError error);
}

@JS('Error')
extension type JsError._(JSObject _) {
  external JsError(String message);
  external set stack(String value);
}

BugsnagClientJs? _client;

Future<void> initBugsnag() async {
  if (!BugsnagConfig.enabled) return;
  try {
    await _loadBugsnagScript();
    final options = <String, Object>{
      'apiKey': BugsnagConfig.apiKey,
      'releaseStage': BugsnagConfig.releaseStage,
      // Keep local/staging builds reporting — the default allow-list is prod-only.
      'enabledReleaseStages': ['production', 'staging', 'dev'],
    }.jsify() as JSObject;
    _client = _bugsnag.start(options);
    _installFlutterHandlers();
  } catch (_) {
    // Never let crash reporting setup block app start.
  }
}

Future<void> _loadBugsnagScript() async {
  if (_client != null) return;
  final existing = web.document.querySelector('script[data-bugsnag]');
  if (existing != null) return;
  final completer = Completer<void>();
  final script = web.document.createElement('script') as web.HTMLScriptElement
    ..src = _bugsnagCdn
    ..async = true
    ..setAttribute('data-bugsnag', '1');
  script.addEventListener(
    'load',
    ((web.Event _) => completer.complete()).toJS,
  );
  script.addEventListener(
    'error',
    ((web.Event _) => completer.completeError(StateError('Bugsnag script failed to load')))
        .toJS,
  );
  web.document.head!.appendChild(script);
  await completer.future;
}

void _installFlutterHandlers() {
  final priorFlutter = FlutterError.onError;
  FlutterError.onError = (details) {
    _notify(details.exception, details.stack);
    priorFlutter?.call(details);
  };

  final priorPlatform = PlatformDispatcher.instance.onError;
  PlatformDispatcher.instance.onError = (error, stack) {
    _notify(error, stack);
    return priorPlatform?.call(error, stack) ?? false;
  };
}

void _notify(Object error, StackTrace? stack) {
  final client = _client;
  if (client == null) return;
  try {
    final jsError = JsError(error.toString());
    if (stack != null) {
      jsError.stack = stack.toString();
    }
    client.notify(jsError);
  } catch (_) {
    // Best-effort only.
  }
}
