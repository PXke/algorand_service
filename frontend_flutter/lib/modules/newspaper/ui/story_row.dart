import 'package:flutter/material.dart';

import '../../../core/config/app_config.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/format.dart';
import '../../../core/ui/lazy_network_image.dart';
import '../sections.dart';

/// THE story entry. One flat, hairline-separated format used everywhere a
/// story is listed — front-page grid, feeds, the latest file, most-read —
/// so the paper reads as one system instead of a mix of card tiles and rows.
///
/// Anatomy: [rank numeral] · kicker + time · serif headline · optional deck ·
/// optional reads/meta line, with a square thumbnail on the right when the
/// story has a real photograph. Variants are flags, not new widgets:
/// [dense] drops the deck and meta (the "in brief" file), [rank] adds the
/// most-read numeral and reads tally.
class StoryRow extends StatefulWidget {
  const StoryRow({
    super.key,
    required this.item,
    required this.onTap,
    this.rank,
    this.dense = false,
    this.first = false,
  });

  final Map<String, dynamic> item;
  final VoidCallback onTap;

  /// Most-read position; renders the broadsheet numeral column + reads line.
  final int? rank;

  /// Headline-only entry (no deck, no meta, smaller thumb).
  final bool dense;

  /// First row of its column: no separating rule above.
  final bool first;

  @override
  State<StoryRow> createState() => _StoryRowState();
}

class _StoryRowState extends State<StoryRow> {
  bool _hovered = false;

  Color _kindColor(BuildContext context) {
    final theme = Theme.of(context);
    final isDark = theme.brightness == Brightness.dark;
    return switch (widget.item['trigger_kind']?.toString() ?? 'editorial') {
      'chain' => isDark ? const Color(0xFF7FC0B8) : const Color(0xFF0E7C70),
      'scheduled' => isDark ? const Color(0xFFA99BCB) : const Color(0xFF6B46C1),
      _ => theme.colorScheme.primary,
    };
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final l10n = context.l10n;
    final item = widget.item;
    final dense = widget.dense;
    final rank = widget.rank;

    final title = item['title']?.toString() ?? l10n.articleUntitled;
    final summary = dense ? '' : stripMarkdown(item['summary']?.toString() ?? '');
    final epoch = item['published_at_epoch'] as int?;
    final views = item['views'] is int ? item['views'] as int : 0;
    final serviceId = item['service_id']?.toString();
    final primary = primaryTag(tagsOf(item));
    final kicker =
        (primary != null ? displayTagLabel(primary) : l10n.navNews).toUpperCase();
    final imageUrl = item['image_url']?.toString();
    final hasPhoto = imageUrl != null &&
        imageUrl.isNotEmpty &&
        !looksLikeLogoUrl(imageUrl);
    final metaLine = dense
        ? ''
        : formatArticleMetaLine(
            context,
            round: (item['trigger_kind']?.toString() == 'chain')
                ? item['trigger_round']
                : null,
            serviceId: serviceId,
          );

    final textColumn = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Flexible(
              child: Text(
                kicker,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.labelSmall?.copyWith(
                  color: _kindColor(context),
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w700,
                  fontSize: 10.5,
                ),
              ),
            ),
            const SizedBox(width: 10),
            Text(
              formatRelativeEpoch(context, epoch),
              style: theme.textTheme.labelSmall
                  ?.copyWith(color: colors.subtle, fontSize: 10.5),
            ),
          ],
        ),
        SizedBox(height: dense ? 6 : 8),
        AnimatedDefaultTextStyle(
          duration: const Duration(milliseconds: 120),
          style: theme.textTheme.titleLarge?.copyWith(
                fontSize: dense ? 17 : 18.5,
                height: 1.3,
                color: _hovered ? theme.colorScheme.primary : null,
              ) ??
              const TextStyle(),
          child: Text(
            title,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (summary.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            summary,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodyMedium?.copyWith(height: 1.5),
          ),
        ],
        if (rank != null && views > 0) ...[
          const SizedBox(height: 6),
          Text(
            l10n.readsCount(views),
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
        ] else if (metaLine.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(
            metaLine,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
        ],
      ],
    );

    final thumbSize = dense ? 64.0 : 88.0;

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: Container(
          decoration: widget.first
              ? null
              : BoxDecoration(
                  border: Border(top: BorderSide(color: colors.border)),
                ),
          padding: EdgeInsets.symmetric(vertical: dense ? 14 : 16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (rank != null) ...[
                SizedBox(
                  width: 34,
                  child: Text(
                    '$rank',
                    style: theme.textTheme.displaySmall?.copyWith(
                      fontSize: 24,
                      height: 1.0,
                      color: rank <= 3 ? colors.accent : colors.subtle,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
              ],
              Expanded(child: textColumn),
              if (hasPhoto) ...[
                const SizedBox(width: 14),
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: SizedBox(
                    width: thumbSize,
                    height: thumbSize,
                    child: ColorFiltered(
                      colorFilter: editorialThumbFilter,
                      child: LazyNetworkImage(
                        url: imageUrl,
                        fit: BoxFit.cover,
                        error: const SizedBox.shrink(),
                      ),
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Two balanced columns of [StoryRow]s with a vertical hairline between them
/// (single column when [twoCol] is false). Row-major order: items 0/1 sit on
/// the first line, 2/3 on the next, so reading order survives the split.
class StoryRowGrid extends StatelessWidget {
  const StoryRowGrid({
    super.key,
    required this.items,
    required this.twoCol,
    required this.onOpen,
    this.dense = false,
    this.ranked = false,
    this.columnMajor = false,
  });

  final List<Map<String, dynamic>> items;
  final bool twoCol;
  final void Function(Map<String, dynamic>) onOpen;
  final bool dense;

  /// Number the entries (most-read module).
  final bool ranked;

  /// Ranks 1..n/2 in the left column (printed-rail order) instead of
  /// alternating. Only sensible together with [ranked].
  final bool columnMajor;

  @override
  Widget build(BuildContext context) {
    StoryRow row(int index, {required bool first}) => StoryRow(
          item: items[index],
          rank: ranked ? index + 1 : null,
          dense: dense,
          first: first,
          onTap: () => onOpen(items[index]),
        );

    if (!twoCol) {
      return Column(
        children: [
          for (var i = 0; i < items.length; i++) row(i, first: i == 0),
        ],
      );
    }

    final left = <int>[];
    final right = <int>[];
    if (columnMajor) {
      final half = (items.length + 1) ~/ 2;
      for (var i = 0; i < items.length; i++) {
        (i < half ? left : right).add(i);
      }
    } else {
      for (var i = 0; i < items.length; i++) {
        (i.isEven ? left : right).add(i);
      }
    }
    Widget column(List<int> indexes) => Expanded(
          child: Column(
            children: [
              for (var i = 0; i < indexes.length; i++)
                row(indexes[i], first: i == 0),
            ],
          ),
        );
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          column(left),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: VerticalDivider(width: 1, color: context.appColors.border),
          ),
          column(right),
        ],
      ),
    );
  }
}
