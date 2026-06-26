import 'package:flutter/material.dart';

import '../../../core/theme/app_theme_extension.dart';

class PriceTickerStrip extends StatelessWidget {
  const PriceTickerStrip({
    super.key,
    required this.assetName,
    required this.priceUsd,
    this.change24hPct,
    this.available = true,
    this.unavailableLabel = 'Price unavailable',
  });

  final String assetName;
  final double priceUsd;
  final double? change24hPct;
  final bool available;
  final String unavailableLabel;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    if (!available) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: theme.cardTheme.color,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: colors.border),
        ),
        child: Row(
          children: [
            Icon(Icons.show_chart, size: 18, color: colors.muted),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                unavailableLabel,
                style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
              ),
            ),
          ],
        ),
      );
    }

    final change = change24hPct;
    final changeColor = change == null
        ? colors.muted
        : change >= 0
            ? const Color(0xFF15803D)
            : const Color(0xFFC0392B);
    final changePrefix = change != null && change > 0 ? '+' : '';

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: colors.border),
      ),
      child: Row(
        children: [
          Icon(Icons.show_chart, size: 18, color: colors.muted),
          const SizedBox(width: 10),
          Text(
            assetName.toUpperCase(),
            style: theme.textTheme.labelLarge?.copyWith(letterSpacing: 0.4),
          ),
          const SizedBox(width: 12),
          Text(
            '\$${priceUsd.toStringAsFixed(priceUsd >= 1 ? 2 : 4)}',
            style: theme.textTheme.titleSmall,
          ),
          if (change != null) ...[
            const SizedBox(width: 10),
            Text(
              '$changePrefix${change.toStringAsFixed(2)}%',
              style: theme.textTheme.bodySmall?.copyWith(
                color: changeColor,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
