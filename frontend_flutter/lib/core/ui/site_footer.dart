import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../l10n/l10n_extensions.dart';
import '../theme/app_theme_extension.dart';
import '../../modules/newspaper/sections.dart';
import 'brand_mark.dart';
import 'page_content.dart';

/// Standing footer: nameplate, section links, about links and a rights line.
/// Present at the foot of the front page and section pages to anchor the paper.
class SiteFooter extends StatelessWidget {
  const SiteFooter({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final year = DateTime.now().year.toString();

    return DecoratedBox(
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: colors.border)),
        color: theme.cardTheme.color,
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 40),
        child: PageContent(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Brand block spans the full width on top; link columns sit below.
              Row(
                children: [
                  const BrandMark(size: 30),
                  const SizedBox(width: 12),
                  Text(l10n.appTitle, style: theme.textTheme.titleLarge),
                ],
              ),
              const SizedBox(height: 8),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: Text(
                  l10n.footerTagline,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: colors.muted,
                    height: 1.5,
                  ),
                ),
              ),
              const SizedBox(height: 28),
              Wrap(
                spacing: 72,
                runSpacing: 24,
                children: [
                  _FooterColumn(
                    heading: l10n.footerSectionsHeading,
                    columns: 2,
                    links: [
                      for (final s in kNewsSections)
                        _FooterLink(
                          label: s.label(context),
                          onTap: () => context.go('/section/${s.slug}'),
                        ),
                    ],
                  ),
                  _FooterColumn(
                    heading: l10n.footerAboutHeading,
                    links: [
                      _FooterLink(
                        label: l10n.navAbout,
                        onTap: () => context.go('/about'),
                      ),
                      _FooterLink(
                        label: l10n.navLatest,
                        onTap: () => context.go('/news'),
                      ),
                      _FooterLink(
                        label: l10n.navSearch,
                        onTap: () => context.go('/search'),
                      ),
                      _FooterLink(
                        label: l10n.navContact,
                        onTap: () => context.go('/contact'),
                      ),
                    ],
                  ),
                ],
              ),
              const SizedBox(height: 28),
              Divider(height: 1, color: colors.border),
              const SizedBox(height: 16),
              Text(
                l10n.footerRights(year),
                style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FooterColumn extends StatelessWidget {
  const _FooterColumn({
    required this.heading,
    required this.links,
    this.columns = 1,
  });

  final String heading;
  final List<_FooterLink> links;

  /// Number of side-by-side link columns under the heading. >1 keeps the
  /// footer short when a section has many links.
  final int columns;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          heading.toUpperCase(),
          style: theme.textTheme.labelSmall?.copyWith(
            color: colors.subtle,
            letterSpacing: 0.9,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 12),
        if (columns <= 1)
          ...links
        else
          _splitColumns(),
      ],
    );
  }

  /// Lays the links into [columns] balanced columns, filling top-to-bottom.
  Widget _splitColumns() {
    final perColumn = (links.length / columns).ceil();
    final groups = <List<_FooterLink>>[];
    for (var i = 0; i < links.length; i += perColumn) {
      groups.add(links.sublist(i, (i + perColumn).clamp(0, links.length)));
    }

    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < groups.length; i++) ...[
          if (i > 0) const SizedBox(width: 40),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: groups[i],
          ),
        ],
      ],
    );
  }
}

class _FooterLink extends StatelessWidget {
  const _FooterLink({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Semantics(
        button: true,
        child: Container(
          constraints: const BoxConstraints(minHeight: 40),
          alignment: Alignment.centerLeft,
          padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
          child: Text(label, style: theme.textTheme.bodyMedium),
        ),
      ),
    );
  }
}
