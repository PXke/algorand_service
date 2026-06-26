import 'package:flutter/material.dart';

import '../../../core/theme/app_theme_extension.dart';

class MetricTileData {
  const MetricTileData({
    required this.id,
    required this.label,
    required this.value,
    this.hint,
    this.available = true,
  });

  final String id;
  final String label;
  final String value;
  final String? hint;
  final bool available;
}

class MetricsDashboardStrip extends StatelessWidget {
  const MetricsDashboardStrip({
    super.key,
    required this.tiles,
    this.loading = false,
  });

  final List<MetricTileData> tiles;
  final bool loading;

  IconData _iconFor(String id) {
    return switch (id) {
      'algo_price' => Icons.payments_outlined,
      'volume_24h' => Icons.bar_chart,
      'market_cap' => Icons.pie_chart_outline,
      'last_round' => Icons.layers_outlined,
      'round_latency' => Icons.speed,
      'articles' => Icons.article_outlined,
      'dex_volume' => Icons.swap_horiz,
      _ => Icons.insights_outlined,
    };
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    if (loading) {
      return SizedBox(
        height: 108,
        child: Center(
          child: SizedBox(
            width: 24,
            height: 24,
            child: CircularProgressIndicator(strokeWidth: 2, color: theme.colorScheme.primary),
          ),
        ),
      );
    }

    if (tiles.isEmpty) {
      return const SizedBox.shrink();
    }

    return SizedBox(
      height: 118,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: tiles.length,
        separatorBuilder: (context, index) => const SizedBox(width: 12),
        itemBuilder: (context, index) {
          final tile = tiles[index];
          final muted = !tile.available;

          return SizedBox(
            width: 164,
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    theme.cardTheme.color ?? colors.panelBackground,
                    colors.accentSoft.withValues(alpha: muted ? 0.15 : 0.35),
                  ],
                ),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: colors.border),
                boxShadow: [
                  BoxShadow(
                    color: colors.cardShadow,
                    blurRadius: 8,
                    offset: const Offset(0, 3),
                  ),
                ],
              ),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      padding: const EdgeInsets.all(5),
                      decoration: BoxDecoration(
                        color: muted
                            ? colors.border.withValues(alpha: 0.4)
                            : colors.accentSoft,
                        borderRadius: BorderRadius.circular(7),
                      ),
                      child: Icon(
                        _iconFor(tile.id),
                        size: 16,
                        color: muted ? colors.muted : colors.accent,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      tile.label,
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: colors.subtle,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      tile.value,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: muted ? colors.muted : null,
                      ),
                    ),
                    if (tile.hint != null && tile.hint!.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        tile.hint!,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: colors.muted,
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
