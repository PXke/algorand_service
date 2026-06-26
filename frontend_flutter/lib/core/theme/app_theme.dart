import 'package:flutter/material.dart';

import 'app_theme_extension.dart';

/// Bundled font families (declared in pubspec.yaml), replacing google_fonts.
const String _sansFamily = 'Inter';
const String _serifFamily = 'Source Serif 4';

/// Editorial light and dark themes: serif display type over a calm,
/// paper-like canvas with a single vivid indigo accent.
class AppTheme {
  static const Color _seed = Color(0xFF4F46E5);

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final isDark = brightness == Brightness.dark;
    final extension = isDark ? AppThemeColors.dark : AppThemeColors.light;

    final base = ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: ColorScheme.fromSeed(
        seedColor: _seed,
        brightness: brightness,
        // Dark primary is a muted slate-indigo (less neon than the old
        // periwinkle) while staying ≥4.5:1 on the dark surface.
        primary: isDark ? const Color(0xFF9AA0C8) : const Color(0xFF4338CA),
        surface: isDark ? const Color(0xFF0E121A) : const Color(0xFFF8F7F4),
      ),
    );

    final scheme = base.colorScheme;
    final onSurface = isDark ? const Color(0xFFEAEEF4) : const Color(0xFF13161C);
    final onSurfaceVariant = isDark ? const Color(0xFF9AA3B2) : const Color(0xFF5C6573);
    final cardColor = extension.panelBackground;
    final appBarColor = isDark ? const Color(0xFF12161F) : Colors.white;

    final body = base.textTheme.apply(
      fontFamily: _sansFamily,
      bodyColor: isDark ? const Color(0xFFCDD4DE) : const Color(0xFF2C3340),
      displayColor: onSurface,
    );
    final serif = base.textTheme.apply(
      fontFamily: _serifFamily,
      bodyColor: onSurface,
      displayColor: onSurface,
    );

    final textTheme = body.copyWith(
      // Serif display scale gives the app its newspaper voice.
      displayLarge: serif.displayLarge?.copyWith(fontWeight: FontWeight.w700),
      displayMedium: serif.displayMedium?.copyWith(fontWeight: FontWeight.w700),
      displaySmall: serif.displaySmall?.copyWith(fontWeight: FontWeight.w700),
      headlineLarge: serif.headlineLarge?.copyWith(
        fontWeight: FontWeight.w700,
        letterSpacing: -0.5,
        height: 1.15,
      ),
      headlineMedium: serif.headlineMedium?.copyWith(
        fontWeight: FontWeight.w700,
        letterSpacing: -0.4,
        height: 1.18,
      ),
      headlineSmall: serif.headlineSmall?.copyWith(
        fontWeight: FontWeight.w700,
        letterSpacing: -0.3,
        height: 1.2,
      ),
      titleLarge: serif.titleLarge?.copyWith(
        fontWeight: FontWeight.w700,
        height: 1.25,
      ),
      titleMedium: body.titleMedium?.copyWith(
        fontWeight: FontWeight.w600,
        color: onSurface,
      ),
      titleSmall: body.titleSmall?.copyWith(
        fontWeight: FontWeight.w600,
        letterSpacing: 0.1,
        color: isDark ? const Color(0xFFB8C0CC) : const Color(0xFF4A5568),
      ),
      bodyLarge: body.bodyLarge?.copyWith(height: 1.6),
      bodyMedium: body.bodyMedium?.copyWith(height: 1.55),
      bodySmall: body.bodySmall?.copyWith(
        color: onSurfaceVariant,
        height: 1.45,
      ),
      labelLarge: body.labelLarge?.copyWith(fontWeight: FontWeight.w600),
      labelMedium: body.labelMedium?.copyWith(color: onSurfaceVariant),
      labelSmall: body.labelSmall?.copyWith(
        letterSpacing: 0.6,
        fontWeight: FontWeight.w600,
      ),
    );

    return base.copyWith(
      extensions: [extension],
      scaffoldBackgroundColor: scheme.surface,
      textTheme: textTheme,
      appBarTheme: AppBarTheme(
        elevation: 0,
        scrolledUnderElevation: 1,
        centerTitle: false,
        backgroundColor: appBarColor,
        foregroundColor: onSurface,
        surfaceTintColor: Colors.transparent,
        shape: Border(bottom: BorderSide(color: extension.border)),
        titleTextStyle: textTheme.titleLarge?.copyWith(fontSize: 19),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: cardColor,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: extension.border),
        ),
        margin: EdgeInsets.zero,
      ),
      dividerTheme: DividerThemeData(
        color: extension.border,
        thickness: 1,
        space: 1,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: cardColor,
        hoverColor: scheme.primary.withValues(alpha: 0.02),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: extension.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: extension.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: scheme.primary, width: 1.6),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        labelStyle: TextStyle(color: onSurfaceVariant),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          elevation: 0,
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 15),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: textTheme.labelLarge,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 15),
          side: BorderSide(color: extension.border),
          foregroundColor: onSurface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          textStyle: textTheme.labelLarge,
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: scheme.primary,
          textStyle: textTheme.labelLarge,
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: cardColor,
        selectedColor: scheme.primaryContainer,
        side: BorderSide(color: extension.border),
        labelStyle: textTheme.labelMedium,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        padding: const EdgeInsets.symmetric(horizontal: 4),
      ),
      drawerTheme: DrawerThemeData(
        backgroundColor: cardColor,
        surfaceTintColor: Colors.transparent,
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      ),
      listTileTheme: ListTileThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: appBarColor,
        indicatorColor: scheme.primaryContainer.withValues(alpha: 0.55),
        selectedIconTheme: IconThemeData(color: scheme.primary, size: 22),
        unselectedIconTheme: IconThemeData(color: onSurfaceVariant, size: 22),
        selectedLabelTextStyle: textTheme.labelMedium!.copyWith(
          color: scheme.primary,
          fontWeight: FontWeight.w700,
        ),
        unselectedLabelTextStyle: textTheme.labelMedium!,
        labelType: NavigationRailLabelType.all,
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStatePropertyAll(scheme.primary.withValues(alpha: 0.35)),
        radius: const Radius.circular(8),
        thickness: const WidgetStatePropertyAll(6),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        ),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        elevation: 2,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      ),
    );
  }
}
