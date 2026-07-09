import 'dart:convert';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:markdown/markdown.dart' as md;

import '../theme/app_theme_extension.dart';

/// Parsed ```chart fence payload from the writer's `chart_data` tool.
class ArticleChartSpec {
  const ArticleChartSpec({
    required this.type,
    required this.title,
    required this.labels,
    required this.series,
  });

  final String type; // line | bar
  final String title;
  final List<String> labels;
  final List<ArticleChartSeries> series;
}

class ArticleChartSeries {
  const ArticleChartSeries({required this.name, required this.values});

  final String name;
  final List<double> values;
}

ArticleChartSpec? parseArticleChartSpec(String raw) {
  try {
    final decoded = jsonDecode(raw.trim());
    if (decoded is! Map) return null;
    final type = (decoded['type']?.toString() ?? 'line').toLowerCase();
    if (type != 'line' && type != 'bar') return null;
    final title = decoded['title']?.toString().trim() ?? '';
    final labels = (decoded['x'] as List?)?.map((e) => e.toString()).toList() ?? const [];
    if (labels.length < 2) return null;
    final rawSeries = decoded['series'];
    if (rawSeries is! List || rawSeries.isEmpty) return null;
    final series = <ArticleChartSeries>[];
    for (final item in rawSeries) {
      if (item is! Map) return null;
      final name = item['name']?.toString().trim() ?? 'Series';
      final ys = item['y'];
      if (ys is! List || ys.length != labels.length) return null;
      final values = <double>[];
      for (final y in ys) {
        final n = y is num ? y.toDouble() : double.tryParse(y.toString());
        if (n == null || !n.isFinite) return null;
        values.add(n);
      }
      series.add(ArticleChartSeries(name: name, values: values));
    }
    if (series.isEmpty) return null;
    return ArticleChartSpec(type: type, title: title, labels: labels, series: series);
  } catch (_) {
    return null;
  }
}

/// Renders a validated article chart (line or bar) inside the prose column.
class ArticleChart extends StatelessWidget {
  const ArticleChart({super.key, required this.spec});

  final ArticleChartSpec spec;

  static const _chartHeight = 240.0;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final palette = <Color>[
      colors.accent,
      theme.colorScheme.secondary,
      const Color(0xFF0E7C70),
      const Color(0xFFB45309),
    ];

    return Semantics(
      label: spec.title.isEmpty ? 'Article chart' : 'Chart: ${spec.title}',
      child: Padding(
        padding: const EdgeInsets.only(top: 8, bottom: 22),
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.35),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (spec.title.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Text(
                      spec.title,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                SizedBox(
                  height: _chartHeight,
                  child: spec.type == 'bar'
                      ? _BarChartBody(spec: spec, palette: palette)
                      : _LineChartBody(spec: spec, palette: palette),
                ),
                if (spec.series.length > 1) ...[
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 14,
                    runSpacing: 6,
                    children: [
                      for (var i = 0; i < spec.series.length; i++)
                        _LegendDot(
                          color: palette[i % palette.length],
                          label: spec.series[i].name,
                        ),
                    ],
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LegendDot extends StatelessWidget {
  const _LegendDot({required this.color, required this.label});

  final Color color;
  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 6),
        Text(label, style: theme.textTheme.labelSmall),
      ],
    );
  }
}

class _LineChartBody extends StatelessWidget {
  const _LineChartBody({required this.spec, required this.palette});

  final ArticleChartSpec spec;
  final List<Color> palette;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.textTheme.bodySmall?.color?.withValues(alpha: 0.75);
    double maxY = 1;
    for (final s in spec.series) {
      for (final v in s.values) {
        maxY = maxY > v ? maxY : v;
      }
    }
    if (maxY <= 0) maxY = 1;

    final showEvery = spec.labels.length > 8 ? (spec.labels.length / 6).ceil() : 1;

    return LineChart(
      LineChartData(
        minY: 0,
        maxY: maxY * 1.12,
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          horizontalInterval: maxY / 4,
          getDrawingHorizontalLine: (_) => FlLine(
            color: theme.dividerColor.withValues(alpha: 0.35),
            strokeWidth: 1,
          ),
        ),
        titlesData: FlTitlesData(
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 42,
              getTitlesWidget: (value, meta) => Text(
                _formatTick(value),
                style: theme.textTheme.labelSmall?.copyWith(fontSize: 10, color: muted),
              ),
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 28,
              interval: 1,
              getTitlesWidget: (value, meta) {
                final i = value.round();
                if (i < 0 || i >= spec.labels.length) return const SizedBox.shrink();
                if (i % showEvery != 0 && i != spec.labels.length - 1) {
                  return const SizedBox.shrink();
                }
                return Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    spec.labels[i],
                    style: theme.textTheme.labelSmall?.copyWith(fontSize: 10, color: muted),
                  ),
                );
              },
            ),
          ),
        ),
        borderData: FlBorderData(show: false),
        lineTouchData: const LineTouchData(enabled: true),
        lineBarsData: [
          for (var si = 0; si < spec.series.length; si++)
            LineChartBarData(
              spots: [
                for (var i = 0; i < spec.series[si].values.length; i++)
                  FlSpot(i.toDouble(), spec.series[si].values[i]),
              ],
              isCurved: spec.labels.length > 3,
              barWidth: 2.5,
              color: palette[si % palette.length],
              dotData: FlDotData(show: spec.labels.length <= 12),
              belowBarData: BarAreaData(
                show: spec.series.length == 1,
                color: palette[si % palette.length].withValues(alpha: 0.10),
              ),
            ),
        ],
      ),
    );
  }
}

class _BarChartBody extends StatelessWidget {
  const _BarChartBody({required this.spec, required this.palette});

  final ArticleChartSpec spec;
  final List<Color> palette;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.textTheme.bodySmall?.color?.withValues(alpha: 0.75);
    double maxY = 1;
    for (final s in spec.series) {
      for (final v in s.values) {
        maxY = maxY > v ? maxY : v;
      }
    }
    if (maxY <= 0) maxY = 1;

    final groups = <BarChartGroupData>[
      for (var i = 0; i < spec.labels.length; i++)
        BarChartGroupData(
          x: i,
          barRods: [
            for (var si = 0; si < spec.series.length; si++)
              BarChartRodData(
                toY: spec.series[si].values[i],
                color: palette[si % palette.length],
                width: spec.series.length > 1 ? 10 : 18,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(4)),
              ),
          ],
        ),
    ];

    return BarChart(
      BarChartData(
        maxY: maxY * 1.12,
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          horizontalInterval: maxY / 4,
          getDrawingHorizontalLine: (_) => FlLine(
            color: theme.dividerColor.withValues(alpha: 0.35),
            strokeWidth: 1,
          ),
        ),
        titlesData: FlTitlesData(
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 42,
              getTitlesWidget: (value, meta) => Text(
                _formatTick(value),
                style: theme.textTheme.labelSmall?.copyWith(fontSize: 10, color: muted),
              ),
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 28,
              getTitlesWidget: (value, meta) {
                final i = value.round();
                if (i < 0 || i >= spec.labels.length) return const SizedBox.shrink();
                return Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    spec.labels[i],
                    style: theme.textTheme.labelSmall?.copyWith(fontSize: 10, color: muted),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                );
              },
            ),
          ),
        ),
        borderData: FlBorderData(show: false),
        barGroups: groups,
      ),
    );
  }
}

String _formatTick(double value) {
  final abs = value.abs();
  if (abs >= 1e9) return '${(value / 1e9).toStringAsFixed(1)}B';
  if (abs >= 1e6) return '${(value / 1e6).toStringAsFixed(1)}M';
  if (abs >= 1e4) return '${(value / 1e3).toStringAsFixed(0)}k';
  if (abs >= 1000) return '${(value / 1e3).toStringAsFixed(1)}k';
  if (abs >= 100) return value.toStringAsFixed(0);
  if (abs >= 1) return value.toStringAsFixed(1);
  return value.toStringAsFixed(3);
}

/// Intercepts ```chart fenced code blocks (class `language-chart`).
class ChartPreElementBuilder extends MarkdownElementBuilder {
  @override
  bool isBlockElement() => true;

  @override
  Widget? visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    if (element.tag != 'pre') return null;
    final children = element.children;
    if (children == null || children.isEmpty) return null;
    final code = children.first;
    if (code is! md.Element || code.tag != 'code') return null;
    final lang = code.attributes['class'] ?? '';
    if (!lang.contains('language-chart')) return null;
    final spec = parseArticleChartSpec(code.textContent);
    if (spec == null) {
      return _ChartParseError(raw: code.textContent.trim());
    }
    return ArticleChart(spec: spec);
  }
}

class _ChartParseError extends StatelessWidget {
  const _ChartParseError({required this.raw});

  final String raw;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Text(
        raw.isEmpty ? 'Chart unavailable' : 'Chart unavailable',
        style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
      ),
    );
  }
}
