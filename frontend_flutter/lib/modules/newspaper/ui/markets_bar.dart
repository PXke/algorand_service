import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/fade_in.dart';
import '../providers/markets_provider.dart';
import 'metrics_dashboard_strip.dart';

/// A thin, persistent ticker of market metrics shown under the masthead — the
/// "markets bar" a finance paper keeps in view. Centered to the content column
/// to sit close to the reader's focus; values cross-fade when they update.
class MarketsBar extends ConsumerStatefulWidget {
  const MarketsBar({super.key});

  @override
  ConsumerState<MarketsBar> createState() => _MarketsBarState();
}

class _MarketsBarState extends ConsumerState<MarketsBar> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    // Gentle live refresh so figures (and their cross-fade) stay current.
    _timer = Timer.periodic(const Duration(seconds: 60), (_) {
      ref.invalidate(marketTilesProvider);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    // valueOrNull keeps the previous figures on screen during a refresh, so the
    // bar never blinks out — it just updates in place.
    final tiles = (ref.watch(marketTilesProvider).asData?.value ?? const <MetricTileData>[])
        .where((t) => t.available)
        .toList();
    if (tiles.isEmpty) {
      return const SizedBox.shrink();
    }

    return FadeIn(
      offset: 0,
      child: Container(
      height: 34,
      decoration: BoxDecoration(
        color: theme.appBarTheme.backgroundColor,
        border: Border(bottom: BorderSide(color: colors.border)),
      ),
      // Use the full bar width: center the chips when they fit, scroll only
      // when the window is genuinely too narrow. A fixed maxWidth here clipped
      // the rightmost metrics on wide screens (no scrollbar on web => looked
      // truncated). minWidth = viewport keeps them centered when there's room.
      child: LayoutBuilder(
        builder: (context, constraints) => SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: ConstrainedBox(
            constraints: BoxConstraints(minWidth: constraints.maxWidth - 32),
            child: Center(
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (var i = 0; i < tiles.length; i++) ...[
                    if (i > 0) _divider(colors),
                    _MetricChip(tile: tiles[i]),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    ),
    );
  }

  Widget _divider(AppThemeColors colors) => Container(
        width: 1,
        height: 14,
        margin: const EdgeInsets.symmetric(horizontal: 14),
        color: colors.border,
      );
}

class _MetricChip extends StatelessWidget {
  const _MetricChip({required this.tile});

  final MetricTileData tile;

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

  Color _hintColor(String hint, AppThemeColors colors) {
    final h = hint.trim();
    if (h.startsWith('+')) return const Color(0xFF15803D);
    if (h.startsWith('-')) return const Color(0xFFC0392B);
    return colors.muted;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final reduceMotion = MediaQuery.maybeOf(context)?.disableAnimations ?? false;

    Widget fade(Widget child, String key) => AnimatedSwitcher(
          duration: Duration(milliseconds: reduceMotion ? 0 : 280),
          transitionBuilder: (c, a) => FadeTransition(opacity: a, child: c),
          child: KeyedSubtree(key: ValueKey(key), child: child),
        );

    return Row(
      children: [
        Icon(_iconFor(tile.id), size: 13, color: colors.subtle),
        const SizedBox(width: 6),
        Text(
          tile.label.toUpperCase(),
          style: theme.textTheme.labelSmall?.copyWith(
            color: colors.subtle,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(width: 8),
        fade(
          Text(
            tile.value,
            style: theme.textTheme.labelMedium?.copyWith(
              fontWeight: FontWeight.w700,
              color: theme.textTheme.titleMedium?.color,
            ),
          ),
          'v:${tile.value}',
        ),
        if (tile.hint != null && tile.hint!.isNotEmpty) ...[
          const SizedBox(width: 6),
          fade(
            Text(
              tile.hint!,
              style: theme.textTheme.labelSmall?.copyWith(
                color: _hintColor(tile.hint!, colors),
              ),
            ),
            'h:${tile.hint}',
          ),
        ],
      ],
    );
  }
}
