import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../theme/app_theme_extension.dart';

/// Renders a ```chart fenced block emitted by the writer agent.
/// Spec: {"type":"line"|"bar","title":..,"x":[..],"series":[{"name":..,"y":[..]}]}
class ArticleChart extends StatelessWidget {
  const ArticleChart({super.key, required this.spec});

  final String spec;

  @override
  Widget build(BuildContext context) {
    Map<String, dynamic> data;
    try {
      data = jsonDecode(spec) as Map<String, dynamic>;
    } catch (_) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    final colors = context.appColors;
    final type = (data['type'] ?? 'line').toString();
    final title = (data['title'] ?? '').toString();
    final x = (data['x'] as List? ?? const []).map((e) => e.toString()).toList();
    final seriesRaw = (data['series'] as List? ?? const []);
    if (x.isEmpty || seriesRaw.isEmpty) return const SizedBox.shrink();

    final series = seriesRaw.whereType<Map>().map((m) {
      final ys = (m['y'] as List? ?? const [])
          .map((e) => (e is num) ? e.toDouble() : double.tryParse(e.toString()) ?? 0.0)
          .toList();
      return _Series((m['name'] ?? '').toString(), ys);
    }).where((s) => s.y.isNotEmpty).toList();
    if (series.isEmpty) return const SizedBox.shrink();

    return Container(
      margin: const EdgeInsets.symmetric(vertical: 16),
      padding: const EdgeInsets.fromLTRB(12, 16, 16, 12),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 12),
              child: Text(title, style: theme.textTheme.titleSmall),
            ),
          SizedBox(
            height: 220,
            child: type == 'bar'
                ? _bar(context, x, series, colors)
                : _line(context, x, series, colors),
          ),
        ],
      ),
    );
  }

  Widget _line(BuildContext context, List<String> x, List<_Series> series, AppThemeColors colors) {
    final accent = Theme.of(context).colorScheme.primary;
    return LineChart(
      LineChartData(
        gridData: FlGridData(show: true, drawVerticalLine: false,
            getDrawingHorizontalLine: (v) => FlLine(color: colors.border, strokeWidth: 0.6)),
        borderData: FlBorderData(show: false),
        titlesData: _titles(x, colors),
        lineBarsData: [
          for (int i = 0; i < series.length; i++)
            LineChartBarData(
              spots: [for (int j = 0; j < series[i].y.length; j++) FlSpot(j.toDouble(), series[i].y[j])],
              isCurved: true,
              color: i == 0 ? accent : colors.accent.withValues(alpha: 0.5),
              barWidth: 2.5,
              dotData: const FlDotData(show: false),
              belowBarData: BarAreaData(show: i == 0, color: accent.withValues(alpha: 0.08)),
            ),
        ],
      ),
    );
  }

  Widget _bar(BuildContext context, List<String> x, List<_Series> series, AppThemeColors colors) {
    final accent = Theme.of(context).colorScheme.primary;
    final s = series.first;
    return BarChart(
      BarChartData(
        gridData: FlGridData(show: true, drawVerticalLine: false,
            getDrawingHorizontalLine: (v) => FlLine(color: colors.border, strokeWidth: 0.6)),
        borderData: FlBorderData(show: false),
        titlesData: _titles(x, colors),
        barGroups: [
          for (int j = 0; j < s.y.length; j++)
            BarChartGroupData(x: j, barRods: [
              BarChartRodData(toY: s.y[j], color: accent, width: 14, borderRadius: BorderRadius.circular(3)),
            ]),
        ],
      ),
    );
  }

  FlTitlesData _titles(List<String> x, AppThemeColors colors) {
    return FlTitlesData(
      topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
      leftTitles: AxisTitles(sideTitles: SideTitles(showTitles: true, reservedSize: 40)),
      bottomTitles: AxisTitles(
        sideTitles: SideTitles(
          showTitles: true,
          reservedSize: 26,
          interval: 1,
          getTitlesWidget: (value, meta) {
            final i = value.toInt();
            if (i < 0 || i >= x.length) return const SizedBox.shrink();
            // Thin out labels when crowded.
            if (x.length > 7 && i % 2 != 0) return const SizedBox.shrink();
            return Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(x[i], style: TextStyle(fontSize: 10, color: colors.muted)),
            );
          },
        ),
      ),
    );
  }
}

class _Series {
  const _Series(this.name, this.y);
  final String name;
  final List<double> y;
}
