import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/admin_provider.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../products.dart';

/// The ▦ app switcher in the masthead: a compact menu of platform products.
/// Scales as products are added — it just renders [platformProducts].
class AppSwitcher extends ConsumerWidget {
  const AppSwitcher({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final colors = context.appColors;
    final isAdmin = ref.watch(isAdminWalletProvider);
    final products = platformProducts(context, isAdmin: isAdmin);
    final location = GoRouterState.of(context).uri.path;

    return PopupMenuButton<String>(
      tooltip: l10n.navApps,
      offset: const Offset(0, 48),
      position: PopupMenuPosition.under,
      constraints: const BoxConstraints(minWidth: 280, maxWidth: 320),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: colors.border),
      ),
      icon: Icon(Icons.apps, size: 22, color: colors.muted),
      onSelected: (route) {
        if (!products.any((p) => p.isActive(location) && p.route == route)) {
          context.go(route);
        }
      },
      itemBuilder: (context) => [
        PopupMenuItem<String>(
          enabled: false,
          height: 34,
          child: Text(
            l10n.navProductsMenuHint,
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                  color: colors.subtle,
                  letterSpacing: 0.8,
                  fontWeight: FontWeight.w700,
                ),
          ),
        ),
        for (final product in products)
          PopupMenuItem<String>(
            value: product.route,
            child: _ProductRow(
              product: product,
              active: product.isActive(location),
            ),
          ),
      ],
    );
  }
}

class _ProductRow extends StatelessWidget {
  const _ProductRow({required this.product, required this.active});

  final PlatformProduct product;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final scheme = theme.colorScheme;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: active ? scheme.primaryContainer.withValues(alpha: 0.6) : colors.accentSoft,
              borderRadius: BorderRadius.circular(10),
            ),
            child: Icon(
              product.icon,
              size: 20,
              color: active ? scheme.primary : colors.accent,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        product.label(context),
                        style: theme.textTheme.titleSmall?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: active ? scheme.primary : null,
                        ),
                      ),
                    ),
                    if (active) ...[
                      const SizedBox(width: 6),
                      Icon(Icons.circle, size: 7, color: scheme.primary),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  product.tagline(context),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: colors.muted,
                    height: 1.35,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
