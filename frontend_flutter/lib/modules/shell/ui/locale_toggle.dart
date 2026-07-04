import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/app_locales.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/l10n/locale_provider.dart';

/// Compact language selector for surfaces without a drawer (e.g. the
/// wide-screen navigation rail).
class LocaleToggleButton extends ConsumerWidget {
  const LocaleToggleButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final locale = ref.watch(localeProvider);
    final selected = locale?.languageCode ?? '';

    return PopupMenuButton<String>(
      tooltip: l10n.navLanguage,
      icon: const Icon(Icons.translate, size: 20),
      initialValue: selected,
      onSelected: (code) =>
          ref.read(localeProvider.notifier).setLocale(localeFromCode(code)),
      itemBuilder: (context) => [
        for (final option in kAppLocaleOptions)
          PopupMenuItem(
            value: option.code,
            child: Text(localeOptionLabel(l10n, option)),
          ),
      ],
    );
  }
}

class LocaleDrawerSection extends ConsumerWidget {
  const LocaleDrawerSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final locale = ref.watch(localeProvider);

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: Text(
              l10n.navLanguage,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    letterSpacing: 0.8,
                    fontWeight: FontWeight.w600,
                  ),
            ),
          ),
          for (final option in kAppLocaleOptions)
            _LocaleOption(
              label: localeOptionLabel(l10n, option),
              selected: option.code.isEmpty
                  ? locale == null
                  : locale?.languageCode == option.code,
              onTap: () =>
                  ref.read(localeProvider.notifier).setLocale(option.locale),
            ),
        ],
      ),
    );
  }
}

class _LocaleOption extends StatelessWidget {
  const _LocaleOption({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return ListTile(
      title: Text(
        label,
        style: TextStyle(fontWeight: selected ? FontWeight.w600 : FontWeight.w500),
      ),
      trailing: selected ? Icon(Icons.check, size: 20, color: scheme.primary) : null,
      selected: selected,
      selectedTileColor: scheme.primaryContainer.withValues(alpha: 0.45),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      onTap: onTap,
    );
  }
}
