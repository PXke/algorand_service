import 'package:flutter/material.dart';

/// A selectable UI language. [code] is empty for “follow system”.
class AppLocaleOption {
  const AppLocaleOption(this.code, this.nativeLabel);

  final String code;
  final String nativeLabel;

  Locale? get locale => code.isEmpty ? null : Locale(code);
}

/// The six most-spoken languages we ship, plus “system”.
/// Native labels are always shown in their own script (not translated).
const List<AppLocaleOption> kAppLocaleOptions = [
  AppLocaleOption('', ''), // label comes from l10n.localeSystem
  AppLocaleOption('en', 'English'),
  AppLocaleOption('zh', '中文'),
  AppLocaleOption('hi', 'हिन्दी'),
  AppLocaleOption('es', 'Español'),
  AppLocaleOption('fr', 'Français'),
  AppLocaleOption('ar', 'العربية'),
];

Locale? localeFromCode(String? code) {
  if (code == null || code.isEmpty || code == 'system') return null;
  for (final opt in kAppLocaleOptions) {
    if (opt.code == code) return opt.locale;
  }
  return null;
}

String localeCodeForStorage(Locale? locale) {
  if (locale == null) return 'system';
  for (final opt in kAppLocaleOptions) {
    if (opt.code == locale.languageCode) return opt.code;
  }
  return 'system';
}
