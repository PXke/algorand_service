import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/l10n/locale_provider.dart';
import 'core/l10n/serialized_l10n_delegate.dart';
import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';
import 'core/theme/deferred_font_loader.dart';
import 'core/theme/theme_mode_provider.dart';
import 'l10n/app_localizations.dart';
import 'modules/shell/ui/auth_chunk_preloader.dart';

class AlgorandPlatformApp extends ConsumerWidget {
  AlgorandPlatformApp({super.key});

  final _router = createAppRouter();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final themeMode = ref.watch(themeModeProvider);
    final locale = ref.watch(localeProvider);

    return DeferredFontLoader(
      child: AuthChunkPreloader(
        child: MaterialApp.router(
          title: 'PXke Algorand Projects',
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          themeMode: themeMode,
          locale: locale,
          localizationsDelegates: const [
            SerializedAppLocalizationsDelegate(),
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          routerConfig: _router,
        ),
      ),
    );
  }
}
