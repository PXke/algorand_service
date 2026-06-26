import 'package:flutter/material.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/page_header.dart';
import '../sections.dart';
import 'article_feed_view.dart';

/// A section landing page (e.g. /section/markets): the latest file filtered to
/// one editorial section.
class SectionPage extends StatelessWidget {
  const SectionPage({super.key, required this.slug});

  final String slug;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final section = sectionForSlug(slug);

    if (section == null) {
      return Center(
        child: EmptyState(
          title: l10n.sectionEmptyTitle,
          message: l10n.sectionEmptyMessage,
          icon: Icons.category_outlined,
        ),
      );
    }

    final label = section.label(context);
    return ArticleFeedView(
      key: ValueKey('section-$slug'),
      section: section,
      header: PageHeader(
        breadcrumb: l10n.navSections,
        title: label,
        icon: section.icon,
        subtitle: l10n.frontPageSectionStories(label),
      ),
      emptyTitle: l10n.sectionEmptyTitle,
      emptyMessage: l10n.sectionEmptyMessage,
    );
  }
}
