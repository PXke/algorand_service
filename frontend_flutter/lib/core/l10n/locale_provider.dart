import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _prefKey = 'app_locale';

/// `null` = follow system locale.
final localeProvider = NotifierProvider<LocaleNotifier, Locale?>(LocaleNotifier.new);

class LocaleNotifier extends Notifier<Locale?> {
  @override
  Locale? build() {
    _restore();
    return null;
  }

  Future<void> _restore() async {
    final prefs = await SharedPreferences.getInstance();
    final code = prefs.getString(_prefKey);
    final locale = switch (code) {
      'en' => const Locale('en'),
      'es' => const Locale('es'),
      'fr' => const Locale('fr'),
      'ar' => const Locale('ar'),
      'system' || null => null,
      _ => null,
    };
    if (ref.mounted) {
      state = locale;
    }
  }

  Future<void> setLocale(Locale? locale) async {
    state = locale;
    final prefs = await SharedPreferences.getInstance();
    final stored = switch (locale?.languageCode) {
      'en' => 'en',
      'es' => 'es',
      'fr' => 'fr',
      'ar' => 'ar',
      null => 'system',
      _ => 'system',
    };
    await prefs.setString(_prefKey, stored);
  }
}
