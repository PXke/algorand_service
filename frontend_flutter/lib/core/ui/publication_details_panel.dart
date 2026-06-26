import 'package:flutter/material.dart';

import '../l10n/l10n_extensions.dart';
import '../theme/app_theme_extension.dart';
import 'meta_row.dart';

/// Compact, collapsible publication metadata below article body.
class PublicationDetailsPanel extends StatelessWidget {
  const PublicationDetailsPanel({
    super.key,
    required this.publisher,
    required this.publishedLabel,
    this.sourceUrl,
    this.dataSourceLabel,
    this.onOpenUrl,
    this.initiallyExpanded = false,
  });

  final String? publisher;
  final String? publishedLabel;
  final String? sourceUrl;
  final String? dataSourceLabel;
  final Future<void> Function(String url)? onOpenUrl;
  final bool initiallyExpanded;

  bool get _hasContent =>
      (publisher != null && publisher!.isNotEmpty) ||
      (publishedLabel != null && publishedLabel!.isNotEmpty) ||
      (sourceUrl != null && sourceUrl!.isNotEmpty) ||
      (dataSourceLabel != null && dataSourceLabel!.isNotEmpty);

  @override
  Widget build(BuildContext context) {
    if (!_hasContent) {
      return const SizedBox.shrink();
    }

    final l10n = context.l10n;
    final colors = context.appColors;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: Theme.of(context).cardTheme.color,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: colors.border),
      ),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          initiallyExpanded: initiallyExpanded,
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 0),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          title: Text(
            l10n.articlePublicationDetails,
            style: Theme.of(context).textTheme.labelLarge?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
          ),
          subtitle: Text(
            l10n.articlePublicationDetailsHint,
            style: Theme.of(context).textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
          children: [
            if (publisher != null && publisher!.isNotEmpty)
              MetaRow(label: l10n.articleMetaPublisher, value: publisher!),
            if (publishedLabel != null && publishedLabel!.isNotEmpty)
              MetaRow(label: l10n.articleMetaPublished, value: publishedLabel!),
            if (dataSourceLabel != null && dataSourceLabel!.isNotEmpty)
              MetaRow(label: l10n.articleMetaDataSource, value: dataSourceLabel!),
            if (sourceUrl != null && sourceUrl!.isNotEmpty)
              MetaRow(
                label: l10n.articleMetaSourceUrl,
                value: sourceUrl!,
                trailing: sourceUrl!.startsWith('http') && onOpenUrl != null
                    ? IconButton(
                        tooltip: l10n.articleOpenInBrowser,
                        onPressed: () => onOpenUrl!(sourceUrl!),
                        icon: const Icon(Icons.open_in_new, size: 18),
                      )
                    : null,
              ),
          ],
        ),
      ),
    );
  }
}
