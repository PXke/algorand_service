import 'package:flutter/material.dart';

/// Shared layout tokens for a consistent, readable content column.
abstract final class AppLayout {
  static const double maxContentWidth = 880;

  /// Narrower measure for long-form reading (article bodies). Tuned to ~70–75
  /// characters per line at the article body size for comfortable sustained
  /// reading.
  static const double maxReadingWidth = 690;
  static const EdgeInsets pagePadding = EdgeInsets.fromLTRB(32, 28, 32, 48);
  static const double sectionGap = 24;
  static const double itemGap = 12;
}
