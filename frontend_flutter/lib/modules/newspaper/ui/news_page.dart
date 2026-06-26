import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/page_header.dart';
import 'article_feed_view.dart';

/// The full chronological file — every story, newest first, optionally
/// filtered to a single publisher via the `service_id` query parameter.
class NewsPage extends StatelessWidget {
  const NewsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final filter = GoRouterState.of(context).uri.queryParameters['service_id'];
    final hasFilter = filter != null && filter.isNotEmpty;

    return ArticleFeedView(
      key: ValueKey('news-${filter ?? ''}'),
      serviceId: hasFilter ? filter : null,
      header: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          PageHeader(
            title: l10n.newsFeedTitle,
            icon: Icons.bolt_outlined,
            subtitle: l10n.newsSubtitleDefault,
          ),
          if (hasFilter)
            _FilterBanner(
              message: l10n.newsFilterShowing(filter),
              onClear: () => context.go('/news'),
              clearLabel: l10n.clearFilter,
            ),
        ],
      ),
      emptyTitle: hasFilter ? l10n.newsEmptyFilteredTitle : l10n.newsEmptyTitle,
      emptyMessage:
          hasFilter ? l10n.newsEmptyFilteredMessage : l10n.newsEmptyMessage,
    );
  }
}

class _FilterBanner extends StatelessWidget {
  const _FilterBanner({
    required this.message,
    required this.onClear,
    required this.clearLabel,
  });

  final String message;
  final VoidCallback onClear;
  final String clearLabel;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final cardColor = Theme.of(context).cardTheme.color;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: cardColor,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: colors.border),
      ),
      child: Row(
        children: [
          Icon(Icons.filter_list, size: 18, color: colors.muted),
          const SizedBox(width: 10),
          Expanded(child: Text(message, style: Theme.of(context).textTheme.bodySmall)),
          TextButton(onPressed: onClear, child: Text(clearLabel)),
        ],
      ),
    );
  }
}
