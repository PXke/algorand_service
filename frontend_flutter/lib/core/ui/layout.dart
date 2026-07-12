import 'package:flutter/material.dart';

/// Shared layout tokens for a consistent, readable content column.
abstract final class AppLayout {
  static const double maxContentWidth = 880;

  /// Narrower measure for long-form reading (article bodies). ~66–70
  /// characters per line at the 19px body size — the readability sweet spot
  /// (research consensus: 45–75 CPL, ~66 optimal; WCAG 1.4.8 caps at 80).
  static const double maxReadingWidth = 660;
  static const EdgeInsets pagePadding = EdgeInsets.fromLTRB(32, 28, 32, 48);
  static const double sectionGap = 24;
  static const double itemGap = 12;
}
