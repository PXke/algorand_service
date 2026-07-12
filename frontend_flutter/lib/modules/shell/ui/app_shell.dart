import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/admin_provider.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/util/analytics_opt_out.dart';
import '../../../core/ui/ambient_background.dart';
import '../../../core/ui/find_shortcut_scope.dart';
import '../../../core/ui/brand_mark.dart';
import '../../../core/deferred/deferred_load_pool.dart';
import '../../../shared/widgets/deferred_widget.dart';
import 'deferred_wallet_app_bar_action.dart';
import '../../newspaper/markets_deferred_gate.dart';
import '../products.dart';
import 'app_switcher.dart';
import 'locale_toggle.dart';
import 'theme_toggle.dart';

/// Width above which the section nav lives inline under the nameplate; below it
/// the sections move into the drawer.
const double _mastheadBreakpoint = 860;

class AppShell extends ConsumerWidget {
  const AppShell({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final location = GoRouterState.of(context).uri.path;
    final colors = context.appColors;
    final theme = Theme.of(context);
    final isAdmin = ref.watch(isAdminWalletProvider);
    // Exclude the owner's own visits from analytics: drop a "don't track" cookie
    // (read by the SSR pageview recorder) whenever the admin wallet connects.
    ref.listen(isAdminWalletProvider, (_, next) => setAnalyticsOptOut(next));
    final width = MediaQuery.sizeOf(context).width;
    final wide = width >= _mastheadBreakpoint;
    final compact = width < 520;

    final navItems = _navItems(context);
    final products = platformProducts(context, isAdmin: isAdmin);

    return Scaffold(
      backgroundColor: theme.scaffoldBackgroundColor,
      appBar: AppBar(
        toolbarHeight: 64,
        titleSpacing: wide ? 24 : null,
        title: _Nameplate(compact: compact, showDate: wide),
        actions: [
          if (location.startsWith('/news/articles/'))
            compact
                ? IconButton(
                    tooltip: l10n.backToFeed,
                    onPressed: () => context.go('/news'),
                    icon: const Icon(Icons.arrow_back, size: 20),
                  )
                : Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: OutlinedButton.icon(
                      onPressed: () => context.go('/news'),
                      icon: const Icon(Icons.arrow_back, size: 16),
                      label: Text(l10n.backToFeed),
                      style: OutlinedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(horizontal: 14),
                        visualDensity: VisualDensity.compact,
                      ),
                    ),
                  ),
          IconButton(
            tooltip: l10n.navSearch,
            onPressed: () => context.go('/search'),
            icon: Icon(
              Icons.search,
              size: 22,
              color: location.startsWith('/search') ? theme.colorScheme.primary : null,
            ),
          ),
          const AppSwitcher(),
          const DeferredWalletAppBarAction(),
          if (!compact) const ThemeToggleButton(),
          if (wide) const LocaleToggleButton(),
          SizedBox(width: compact ? 4 : 12),
        ],
        bottom: wide
            ? PreferredSize(
                preferredSize: const Size.fromHeight(45),
                child: _SectionNavBar(items: navItems, location: location),
              )
            : null,
      ),
      drawer: wide
          ? null
          : Drawer(
              child: SafeArea(
                child: ListView(
                  padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 8),
                  children: [
                    _DrawerHeader(),
                    Divider(height: 1, color: colors.border),
                    _DrawerGroupLabel(l10n.navApps),
                    for (final product in products)
                      _NavTile(
                        item: _NavItem(
                          label: product.label(context),
                          path: product.route,
                          icon: product.icon,
                          exact: product.route == '/',
                        ),
                        selected: product.isActive(location),
                      ),
                    const SizedBox(height: 8),
                    Divider(height: 1, color: colors.border),
                    _DrawerGroupLabel(l10n.navNews.toUpperCase()),
                    for (final item in navItems)
                      _NavTile(item: item, selected: _isSelected(location, item)),
                    const SizedBox(height: 8),
                    Divider(height: 1, color: colors.border),
                    const ThemeModeDrawerSection(),
                    Divider(height: 1, color: colors.border),
                    const LocaleDrawerSection(),
                  ],
                ),
              ),
            ),
      body: Column(
        children: [
          if (_showMarketsBar(location))
            DeferredWidget(
              () => loadDeferredWithRetry(loadMarketsModule),
              buildMarketsBar,
              placeholder: const SizedBox(height: 34),
            ),
          Expanded(
            child: FindShortcutScope(
              child: AmbientBackground(child: child),
            ),
          ),
        ],
      ),
    );
  }

  /// Newspaper section nav (the section axis). Products (Search, Suggestions,
  /// Admin) live in the app switcher (▦), not here.
  List<_NavItem> _navItems(BuildContext context) {
    final l10n = context.l10n;
    return <_NavItem>[
      _NavItem(
        label: l10n.navLatest,
        path: '/news',
        icon: Icons.bolt_outlined,
        exact: true,
      ),
      _NavItem(
        label: l10n.navHot,
        path: '/hot',
        icon: Icons.local_fire_department_outlined,
      ),
      // /topics is the cloud; /topic/:tag pages keep the same tab lit.
      _NavItem(
        label: l10n.navTopics,
        path: '/topics',
        icon: Icons.tag,
        matchPrefix: '/topic',
      ),
      // Search is content-finding, not a separate product — a news site
      // reader expects it in the news nav (the app-bar icon stays too).
      _NavItem(label: l10n.navSearch, path: '/search', icon: Icons.search),
      _NavItem(label: l10n.navAbout, path: '/about', icon: Icons.info_outline),
      _NavItem(label: l10n.navContact, path: '/contact', icon: Icons.mail_outline),
    ];
  }

  static bool _isSelected(String location, _NavItem item) {
    if (item.exact) return location == item.path;
    return location.startsWith(item.matchPrefix ?? item.path);
  }

  /// Markets API is deferred; only mount the bar on newspaper feed routes.
  static bool _showMarketsBar(String location) {
    return location == '/' ||
        location == '/news' ||
        location == '/hot' ||
        location == '/topics' ||
        location.startsWith('/topic/');
  }
}

/// Serif wordmark with an optional dateline — the paper's nameplate.
class _Nameplate extends StatelessWidget {
  const _Nameplate({required this.compact, required this.showDate});

  final bool compact;
  final bool showDate;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final locale = Localizations.localeOf(context).toLanguageTag();
    final dateLabel = _formatDate(locale);

    return InkWell(
      onTap: () => context.go('/'),
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 2),
        child: Row(
          children: [
            const BrandMark(),
            const SizedBox(width: 12),
            Flexible(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    compact ? 'PXke' : l10n.appTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.4,
                      fontSize: compact ? 20 : 25,
                    ),
                  ),
                  if (showDate)
                    Text(
                      dateLabel,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: colors.subtle,
                        letterSpacing: 0.3,
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Localised long date for the dateline, falling back to the default locale
  /// when symbol data for [locale] has not been loaded.
  static String _formatDate(String locale) {
    final now = DateTime.now();
    try {
      return DateFormat.yMMMMEEEEd(locale).format(now);
    } catch (_) {
      return DateFormat.yMMMMEEEEd().format(now);
    }
  }
}

/// Inline section navigation under the nameplate (wide layout).
class _SectionNavBar extends StatelessWidget {
  const _SectionNavBar({required this.items, required this.location});

  final List<_NavItem> items;
  final String location;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final theme = Theme.of(context);

    return Container(
      height: 45,
      decoration: BoxDecoration(
        color: theme.appBarTheme.backgroundColor,
        border: Border(top: BorderSide(color: colors.border)),
      ),
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 18),
        children: [
          for (final item in items)
            _MastheadTab(
              item: item,
              selected: AppShell._isSelected(location, item),
            ),
        ],
      ),
    );
  }
}

class _MastheadTab extends StatefulWidget {
  const _MastheadTab({required this.item, required this.selected});

  final _NavItem item;
  final bool selected;

  @override
  State<_MastheadTab> createState() => _MastheadTabState();
}

class _MastheadTabState extends State<_MastheadTab> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final scheme = theme.colorScheme;
    final active = widget.selected;
    final color = active
        ? scheme.primary
        : _hovered
            ? (theme.textTheme.titleMedium?.color ?? scheme.onSurface)
            : colors.muted;

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: () => context.go(widget.item.path),
        behavior: HitTestBehavior.opaque,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 13),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Spacer(),
              AnimatedDefaultTextStyle(
                duration: const Duration(milliseconds: 160),
                curve: Curves.easeOut,
                style: theme.textTheme.labelLarge!.copyWith(
                  color: color,
                  fontWeight: active ? FontWeight.w700 : FontWeight.w600,
                  letterSpacing: 0.3,
                ),
                child: Text(widget.item.label),
              ),
              const SizedBox(height: 7),
              AnimatedContainer(
                duration: const Duration(milliseconds: 200),
                curve: Curves.easeOutCubic,
                height: 2.5,
                width: active ? 22 : (_hovered ? 12 : 0),
                decoration: BoxDecoration(
                  color: active
                      ? scheme.primary
                      : colors.muted.withValues(alpha: 0.5),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DrawerHeader extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;

    return Container(
      margin: const EdgeInsets.all(12),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: theme.scaffoldBackgroundColor,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const BrandMark(),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  l10n.appTitle,
                  style: theme.textTheme.titleLarge?.copyWith(fontSize: 18),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            l10n.appTagline,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
        ],
      ),
    );
  }
}

class _NavItem {
  const _NavItem({
    required this.label,
    required this.path,
    required this.icon,
    this.exact = false,
    this.matchPrefix,
  });

  final String label;
  final String path;
  final IconData icon;
  final bool exact;

  /// Highlight prefix when it differs from [path] (e.g. the Topics tab lives
  /// at /topics but should stay lit on /topic/:tag pages).
  final String? matchPrefix;
}

class _DrawerGroupLabel extends StatelessWidget {
  const _DrawerGroupLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Text(
        text,
        style: theme.textTheme.labelSmall?.copyWith(
          color: colors.subtle,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _NavTile extends StatelessWidget {
  const _NavTile({required this.item, required this.selected});

  final _NavItem item;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final colors = context.appColors;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: ListTile(
        leading: Icon(
          item.icon,
          size: 21,
          color: selected ? scheme.primary : colors.muted,
        ),
        minLeadingWidth: 24,
        title: Text(
          item.label,
          style: TextStyle(
            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
            color: selected ? scheme.primary : null,
          ),
        ),
        selected: selected,
        selectedTileColor: scheme.primaryContainer.withValues(alpha: 0.45),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        onTap: () {
          Navigator.pop(context);
          context.go(item.path);
        },
      ),
    );
  }
}
