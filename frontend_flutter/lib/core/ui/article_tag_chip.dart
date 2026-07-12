import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../theme/app_theme_extension.dart';

class ArticleTagChip extends StatelessWidget {
  const ArticleTagChip({
    super.key,
    required this.label,
    this.compact = false,
    this.linkToTopic = false,
  });

  final String label;
  final bool compact;

  /// When set, the chip navigates to the tag's topic page. Off inside story
  /// cards, where the whole card is already the article's tap target.
  final bool linkToTopic;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final theme = Theme.of(context);

    final chip = Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 8 : 10,
        vertical: compact ? 3 : 5,
      ),
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [
            colors.accentSoft,
            colors.accentSoft.withValues(alpha: 0.5),
          ],
        ),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: colors.border.withValues(alpha: 0.7)),
      ),
      child: Text(
        label,
        style: (compact ? theme.textTheme.labelSmall : theme.textTheme.labelMedium)?.copyWith(
          fontWeight: FontWeight.w500,
        ),
      ),
    );
    if (!linkToTopic) return chip;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: () => context
            .go('/topic/${Uri.encodeComponent(label.trim().toLowerCase())}'),
        child: chip,
      ),
    );
  }
}

class ArticleTagRow extends StatelessWidget {
  const ArticleTagRow({
    super.key,
    required this.tags,
    this.compact = false,
    this.linkToTopic = false,
  });

  final List<String> tags;
  final bool compact;
  final bool linkToTopic;

  @override
  Widget build(BuildContext context) {
    if (tags.isEmpty) {
      return const SizedBox.shrink();
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final tag in tags)
          ArticleTagChip(label: tag, compact: compact, linkToTopic: linkToTopic),
      ],
    );
  }
}
