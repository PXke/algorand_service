import 'package:flutter/material.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/theme/app_theme_extension.dart';

/// The paper's signature module: the chain's day in numbers, set like a
/// broadsheet market panel — a display-serif ALGO price over a real 7-day
/// sparkline, flanked by the figures an Algorand reader checks daily. This is
/// the identity moment: an autonomous newsroom sitting on live chain data.
/// (The slim ribbon at the top is chrome; this is editorial.)
class ByTheNumbers extends StatelessWidget {
  const ByTheNumbers({
    super.key,
    required this.price,
    required this.history,
  });

  /// /api/v1/metrics/price payload.
  final Map<String, dynamic> price;

  /// (epoch, price) points, oldest first — /api/v1/metrics/price/history.
  final List<({int epoch, double price})> history;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final l10n = context.l10n;

    final priceUsd = (price['price_usd'] as num?)?.toDouble() ?? 0;
    final change = (price['change_24h_pct'] as num?)?.toDouble();
    final marketCap = (price['market_cap_usd'] as num?)?.toDouble();
    final volume = (price['volume_24h_usd'] as num?)?.toDouble();
    if (priceUsd <= 0) return const SizedBox.shrink();

    final up = (change ?? 0) >= 0;
    final changeColor = up
        ? (theme.brightness == Brightness.dark
            ? const Color(0xFF7FC0A8)
            : const Color(0xFF0E7C50))
        : theme.colorScheme.error;

    final wide = MediaQuery.sizeOf(context).width >= 700;

    final figures = [
      if (marketCap != null) (l10n.byTheNumbersMarketCap, _compactUsd(marketCap)),
      if (volume != null) (l10n.byTheNumbersVolume, _compactUsd(volume)),
    ];

    // One flat band, single Column: label row, big price line with the
    // supporting figures inline at the right, full-width sparkline, dateline.
    return Container(
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(color: colors.accent, width: 3),
          bottom: BorderSide(color: colors.border),
        ),
      ),
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            'ALGO',
            style: theme.textTheme.labelSmall?.copyWith(
              color: colors.subtle,
              letterSpacing: 1.4,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 6),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                '\$${priceUsd.toStringAsFixed(4)}',
                style: theme.textTheme.displaySmall?.copyWith(
                  fontSize: wide ? 44 : 34,
                  height: 1.0,
                  letterSpacing: -1,
                ),
              ),
              if (change != null) ...[
                const SizedBox(width: 12),
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    '${up ? '▲' : '▼'} ${change.abs().toStringAsFixed(2)}%',
                    style: theme.textTheme.titleSmall?.copyWith(
                      color: changeColor,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ],
              const Spacer(),
              if (wide)
                for (final figure in figures)
                  Padding(
                    padding: const EdgeInsets.only(left: 28),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          figure.$1.toUpperCase(),
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: colors.subtle,
                            letterSpacing: 1.2,
                            fontWeight: FontWeight.w700,
                            fontSize: 10.5,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          figure.$2,
                          style: theme.textTheme.headlineSmall
                              ?.copyWith(fontSize: 22, height: 1.0),
                        ),
                      ],
                    ),
                  ),
            ],
          ),
          if (history.length >= 2) ...[
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              height: 44,
              child: CustomPaint(
                painter: _SparklinePainter(
                  points: history,
                  lineColor: colors.accent,
                  fillColor: colors.accent.withValues(alpha: 0.10),
                ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              l10n.byTheNumbersRange,
              style: theme.textTheme.labelSmall
                  ?.copyWith(color: colors.subtle, fontSize: 10.5),
            ),
          ],
          if (!wide && figures.isNotEmpty) ...[
            const SizedBox(height: 16),
            Row(
              children: [
                for (final figure in figures)
                  Padding(
                    padding: const EdgeInsets.only(right: 28),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          figure.$1.toUpperCase(),
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: colors.subtle,
                            letterSpacing: 1.2,
                            fontWeight: FontWeight.w700,
                            fontSize: 10.5,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          figure.$2,
                          style: theme.textTheme.headlineSmall
                              ?.copyWith(fontSize: 20, height: 1.0),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  static String _compactUsd(double value) {
    if (value >= 1e9) return '\$${(value / 1e9).toStringAsFixed(2)}B';
    if (value >= 1e6) return '\$${(value / 1e6).toStringAsFixed(1)}M';
    if (value >= 1e3) return '\$${(value / 1e3).toStringAsFixed(1)}K';
    return '\$${value.toStringAsFixed(0)}';
  }
}

class _SparklinePainter extends CustomPainter {
  const _SparklinePainter({
    required this.points,
    required this.lineColor,
    required this.fillColor,
  });

  final List<({int epoch, double price})> points;
  final Color lineColor;
  final Color fillColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (points.length < 2) return;
    final minEpoch = points.first.epoch;
    final maxEpoch = points.last.epoch;
    var minPrice = points.first.price;
    var maxPrice = points.first.price;
    for (final p in points) {
      if (p.price < minPrice) minPrice = p.price;
      if (p.price > maxPrice) maxPrice = p.price;
    }
    // Plain arithmetic only: web ints are JS numbers, so tricks like 1 << 62
    // misbehave — and an exception inside paint() kills the layer silently.
    final rawSpan = maxEpoch - minEpoch;
    final epochSpan = rawSpan > 0 ? rawSpan : 1;
    final priceSpan = (maxPrice - minPrice).abs() < 1e-12
        ? 1.0
        : (maxPrice - minPrice);

    Offset toOffset(({int epoch, double price}) p) => Offset(
          (p.epoch - minEpoch) / epochSpan * size.width,
          // 8% headroom top and bottom so the line never kisses the edge.
          size.height * 0.92 -
              ((p.price - minPrice) / priceSpan) * size.height * 0.84,
        );

    final line = Path()..moveTo(toOffset(points.first).dx, toOffset(points.first).dy);
    for (final p in points.skip(1)) {
      final o = toOffset(p);
      line.lineTo(o.dx, o.dy);
    }

    final fill = Path.from(line)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(fill, Paint()..color = fillColor);
    canvas.drawPath(
      line,
      Paint()
        ..color = lineColor
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.6
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(covariant _SparklinePainter old) =>
      old.points != points || old.lineColor != lineColor;
}
