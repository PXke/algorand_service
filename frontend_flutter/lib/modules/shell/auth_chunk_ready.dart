import 'package:flutter/foundation.dart';

/// Flips to true once the deferred auth library finishes downloading.
final authChunkReady = ValueNotifier<bool>(false);
