import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../l10n/app_localizations.dart';
import 'app_locales.dart';

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
    final locale = localeFromCode(code == 'system' ? null : code);
    if (ref.mounted) {
      state = locale;
    }
  }

  Future<void> setLocale(Locale? locale) async {
    state = locale;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefKey, localeCodeForStorage(locale));
  }
}

/// Language code for news API content (`?lang=`). Uses the explicit picker
/// choice when set, otherwise the active Material locale (system language).
String contentLanguageCode(WidgetRef ref, BuildContext context) {
  final picked = ref.read(localeProvider);
  if (picked != null && picked.languageCode.isNotEmpty) {
    return picked.languageCode;
  }
  return Localizations.localeOf(context).languageCode;
}

/// Native language name for the locale picker (always in the target script).
String localeOptionLabel(AppLocalizations l10n, AppLocaleOption option) {
  if (option.code.isEmpty) return l10n.localeSystem;
  return switch (option.code) {
    'en' => l10n.localeEnglish,
    'zh' => l10n.localeChinese,
    'hi' => l10n.localeHindi,
    'es' => l10n.localeSpanish,
    'fr' => l10n.localeFrench,
    'ar' => l10n.localeArabic,
    'ru' => l10n.localeRussian,
    'fa' => l10n.localeDari,
    'ps' => l10n.localePashto,
    _ => option.nativeLabel,
  };
}
