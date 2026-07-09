import 'package:flutter/material.dart';

import 'markets_entry.dart' deferred as markets;

/// Memoized load for the markets deferred chunk. Callers must go through
/// [loadDeferredWithRetry] / [serializeDeferredLoad] — do not nest serialize here
/// or the global queue deadlocks.
Future<void>? _library;

Future<void> loadMarketsModule() => _library ??= markets.loadLibrary();

Widget buildMarketsBar() => markets.MarketsBar();
