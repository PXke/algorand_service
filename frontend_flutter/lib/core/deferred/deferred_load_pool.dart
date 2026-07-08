import 'dart:async';

/// Serializes deferred [loadLibrary] calls on web. dart2js's multi-loader can
/// throw `DeferredLoadException: Success callback … not loaded` when several
/// chunks start downloading concurrently.
Future<void> _queue = Future<void>.value();

Future<void> serializeDeferredLoad(Future<void> Function() load) {
  final done = Completer<void>();
  _queue = _queue.catchError((_) {}).then((_) async {
    try {
      await load();
      done.complete();
    } catch (e, st) {
      done.completeError(e, st);
      rethrow;
    }
  });
  return done.future;
}

/// Run [load] (memoized [loadLibrary] is fine) one-at-a-time with retries.
/// Do not call [serializeDeferredLoad] inside [load] — that deadlocks the queue.
Future<void> loadDeferredWithRetry(
  Future<void> Function() load, {
  int attempts = 3,
}) async {
  Object? lastError;
  for (var i = 0; i < attempts; i++) {
    try {
      await serializeDeferredLoad(load);
      return;
    } catch (e) {
      lastError = e;
      if (i < attempts - 1) {
        await Future<void>.delayed(Duration(milliseconds: 80 * (i + 1)));
      }
    }
  }
  throw lastError!;
}
