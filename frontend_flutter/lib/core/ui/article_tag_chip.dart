import 'package:flutter/material.dart';

import '../theme/app_theme_extension.dart';

class ArticleTagChip extends StatelessWidget {
  const ArticleTagChip({super.key, required this.label, this.compact = false});

  final String label;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final theme = Theme.of(context);

    return Container(
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
  }
}

class ArticleTagRow extends StatelessWidget {
  const ArticleTagRow({super.key, required this.tags, this.compact = false});

  final List<String> tags;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    if (tags.isEmpty) {
      return const SizedBox.shrink();
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [for (final tag in tags) ArticleTagChip(label: tag, compact: compact)],
    );
  }
}
