import 'package:flutter/material.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../models/source_kind.dart';

/// Compact source-type label for cards and headers.
class SourceKindChip extends StatelessWidget {
  const SourceKindChip({
    super.key,
    required this.kind,
    this.compact = false,
  });

  final SourceKind kind;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = context.l10n;
    final fg = kind.foreground(theme.colorScheme);

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: compact ? 8 : 10,
        vertical: compact ? 3 : 5,
      ),
      decoration: BoxDecoration(
        color: kind.background(theme.colorScheme),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: fg.withValues(alpha: 0.2)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(kind.icon, size: compact ? 13 : 14, color: fg),
          const SizedBox(width: 5),
          Text(
            kind.label(l10n),
            style: theme.textTheme.labelSmall?.copyWith(
              color: fg,
              fontWeight: FontWeight.w600,
              fontSize: compact ? 11 : 12,
              letterSpacing: 0.15,
            ),
          ),
        ],
      ),
    );
  }
}
