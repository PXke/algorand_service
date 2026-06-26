import 'package:flutter/material.dart';

/// Semantic colors that adapt to light and dark themes.
@immutable
class AppThemeColors extends ThemeExtension<AppThemeColors> {
  const AppThemeColors({
    required this.panelBackground,
    required this.muted,
    required this.subtle,
    required this.border,
    required this.calloutBackground,
    required this.accent,
    required this.accentSoft,
    required this.heroGradientStart,
    required this.heroGradientEnd,
    required this.cardShadow,
    required this.cardHoverShadow,
  });

  final Color panelBackground;
  final Color muted;
  final Color subtle;
  final Color border;
  final Color calloutBackground;

  /// Vivid accent used for highlights, links and small flourishes.
  final Color accent;

  /// Soft tinted background for accent-colored surfaces.
  final Color accentSoft;
  final Color heroGradientStart;
  final Color heroGradientEnd;
  final Color cardShadow;
  final Color cardHoverShadow;

  static const light = AppThemeColors(
    panelBackground: Color(0xFFFFFFFF),
    muted: Color(0xFF5C6573),
    subtle: Color(0xFF6B7480),
    border: Color(0xFFDADDE4),
    calloutBackground: Color(0xFFEFEDFC),
    accent: Color(0xFF4F46E5),
    accentSoft: Color(0xFFEDEBFE),
    heroGradientStart: Color(0xFF171238),
    heroGradientEnd: Color(0xFF4338CA),
    cardShadow: Color(0x0F101828),
    cardHoverShadow: Color(0x24101828),
  );

  static const dark = AppThemeColors(
    panelBackground: Color(0xFF161B24),
    muted: Color(0xFF9AA3B2),
    subtle: Color(0xFF6B7585),
    border: Color(0xFF272F3D),
    calloutBackground: Color(0xFF1C2030),
    // Muted slate-indigo accent (de-neoned) + a neutral soft tint.
    accent: Color(0xFF9AA0C8),
    accentSoft: Color(0xFF20242E),
    heroGradientStart: Color(0xFF1B2030),
    heroGradientEnd: Color(0xFF2E3550),
    cardShadow: Color(0x33000000),
    cardHoverShadow: Color(0x59000000),
  );

  @override
  AppThemeColors copyWith({
    Color? panelBackground,
    Color? muted,
    Color? subtle,
    Color? border,
    Color? calloutBackground,
    Color? accent,
    Color? accentSoft,
    Color? heroGradientStart,
    Color? heroGradientEnd,
    Color? cardShadow,
    Color? cardHoverShadow,
  }) {
    return AppThemeColors(
      panelBackground: panelBackground ?? this.panelBackground,
      muted: muted ?? this.muted,
      subtle: subtle ?? this.subtle,
      border: border ?? this.border,
      calloutBackground: calloutBackground ?? this.calloutBackground,
      accent: accent ?? this.accent,
      accentSoft: accentSoft ?? this.accentSoft,
      heroGradientStart: heroGradientStart ?? this.heroGradientStart,
      heroGradientEnd: heroGradientEnd ?? this.heroGradientEnd,
      cardShadow: cardShadow ?? this.cardShadow,
      cardHoverShadow: cardHoverShadow ?? this.cardHoverShadow,
    );
  }

  @override
  AppThemeColors lerp(ThemeExtension<AppThemeColors>? other, double t) {
    if (other is! AppThemeColors) {
      return this;
    }
    return AppThemeColors(
      panelBackground: Color.lerp(panelBackground, other.panelBackground, t)!,
      muted: Color.lerp(muted, other.muted, t)!,
      subtle: Color.lerp(subtle, other.subtle, t)!,
      border: Color.lerp(border, other.border, t)!,
      calloutBackground: Color.lerp(calloutBackground, other.calloutBackground, t)!,
      accent: Color.lerp(accent, other.accent, t)!,
      accentSoft: Color.lerp(accentSoft, other.accentSoft, t)!,
      heroGradientStart: Color.lerp(heroGradientStart, other.heroGradientStart, t)!,
      heroGradientEnd: Color.lerp(heroGradientEnd, other.heroGradientEnd, t)!,
      cardShadow: Color.lerp(cardShadow, other.cardShadow, t)!,
      cardHoverShadow: Color.lerp(cardHoverShadow, other.cardHoverShadow, t)!,
    );
  }
}

extension AppThemeColorsContext on BuildContext {
  AppThemeColors get appColors =>
      Theme.of(this).extension<AppThemeColors>() ?? AppThemeColors.light;
}
