import 'package:flutter/widgets.dart';

import '../deferred/deferred_load_pool.dart';
import '../../l10n/app_localizations.dart';

/// Wraps [AppLocalizations.delegate] so locale `loadLibrary()` calls share
/// the global deferred queue (same race protection as routes / markets / auth).
class SerializedAppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const SerializedAppLocalizationsDelegate();

  @override
  bool isSupported(Locale locale) =>
      AppLocalizations.delegate.isSupported(locale);

  @override
  bool shouldReload(covariant LocalizationsDelegate<AppLocalizations> old) =>
      false;

  @override
  Future<AppLocalizations> load(Locale locale) async {
    late AppLocalizations loaded;
    await serializeDeferredLoad(() async {
      loaded = await AppLocalizations.delegate.load(locale);
    });
    return loaded;
  }
}
