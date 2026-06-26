import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/theme_mode_provider.dart';

class ThemeToggleButton extends ConsumerWidget {
  const ThemeToggleButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return IconButton(
      tooltip: isDark ? l10n.themeSwitchToLight : l10n.themeSwitchToDark,
      onPressed: () => ref.read(themeModeProvider.notifier).toggleLightDark(),
      icon: Icon(isDark ? Icons.light_mode_outlined : Icons.dark_mode_outlined),
    );
  }
}

class ThemeModeDrawerSection extends ConsumerWidget {
  const ThemeModeDrawerSection({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final mode = ref.watch(themeModeProvider);

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: Text(
              l10n.navAppearance,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    letterSpacing: 0.8,
                    fontWeight: FontWeight.w600,
                  ),
            ),
          ),
          _ThemeOption(
            label: l10n.themeLight,
            icon: Icons.light_mode_outlined,
            selected: mode == ThemeMode.light,
            onTap: () => ref.read(themeModeProvider.notifier).setMode(ThemeMode.light),
          ),
          _ThemeOption(
            label: l10n.themeDark,
            icon: Icons.dark_mode_outlined,
            selected: mode == ThemeMode.dark,
            onTap: () => ref.read(themeModeProvider.notifier).setMode(ThemeMode.dark),
          ),
          _ThemeOption(
            label: l10n.themeSystem,
            icon: Icons.settings_brightness_outlined,
            selected: mode == ThemeMode.system,
            onTap: () => ref.read(themeModeProvider.notifier).setMode(ThemeMode.system),
          ),
        ],
      ),
    );
  }
}

class _ThemeOption extends StatelessWidget {
  const _ThemeOption({
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return ListTile(
      leading: Icon(icon, size: 22),
      title: Text(label, style: TextStyle(fontWeight: selected ? FontWeight.w600 : FontWeight.w500)),
      trailing: selected ? Icon(Icons.check, size: 20, color: scheme.primary) : null,
      selected: selected,
      selectedTileColor: scheme.primaryContainer.withValues(alpha: 0.45),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      onTap: onTap,
    );
  }
}
