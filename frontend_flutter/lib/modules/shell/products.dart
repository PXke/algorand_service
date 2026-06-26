import 'package:flutter/material.dart';

import '../../core/config/app_config.dart';
import '../../core/l10n/l10n_extensions.dart';

/// A top-level product on the platform — the unit the app switcher (▦) lists.
///
/// The platform is one Flutter web app (see docs/architecture/products-and-bricks.md);
/// products are surfaces inside it, not separate apps. Add a product by adding
/// one entry to [platformProducts] — the switcher and mobile drawer pick it up.
class PlatformProduct {
  const PlatformProduct({
    required this.id,
    required this.route,
    required this.icon,
    required this.labelOf,
    required this.taglineOf,
  });

  final String id;
  final String route;
  final IconData icon;
  final String Function(BuildContext) labelOf;
  final String Function(BuildContext) taglineOf;

  String label(BuildContext context) => labelOf(context);
  String tagline(BuildContext context) => taglineOf(context);

  /// True when the current location belongs to this product.
  bool isActive(String location) {
    if (route == '/') {
      // The newspaper owns the front page plus its reading/section surfaces.
      return location == '/' ||
          location.startsWith('/news') ||
          location.startsWith('/section') ||
          location == '/about';
    }
    return location.startsWith(route);
  }
}

/// The products visible to the current viewer. Order is the switcher order.
List<PlatformProduct> platformProducts(
  BuildContext context, {
  required bool isAdmin,
}) {
  return [
    PlatformProduct(
      id: 'newspaper',
      route: '/',
      icon: Icons.menu_book_outlined,
      labelOf: (c) => c.l10n.navNews,
      taglineOf: (c) => c.l10n.homeNewsDescription,
    ),
    PlatformProduct(
      id: 'search',
      route: '/search',
      icon: Icons.search,
      labelOf: (c) => c.l10n.navSearch,
      taglineOf: (c) => c.l10n.homeSearchDescription,
    ),
    if (AppConfig.instance.suggestionsEnabled)
      PlatformProduct(
        id: 'suggestions',
        route: '/suggestions',
        icon: Icons.lightbulb_outline,
        labelOf: (c) => c.l10n.navSuggestions,
        taglineOf: (c) => c.l10n.homeSuggestionsDescription,
      ),
    if (isAdmin)
      PlatformProduct(
        id: 'admin',
        route: '/admin',
        icon: Icons.admin_panel_settings_outlined,
        labelOf: (c) => c.l10n.navAdmin,
        taglineOf: (c) => c.l10n.adminSubtitle,
      ),
  ];
}
