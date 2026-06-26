import 'dart:math' as math;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';

/// Admin tab: first-party traffic analytics, recorded server-side from the SSR
/// document routes (no client JS). Shows pageviews split human vs bot, the top
/// pages, and the top referrers — so you can see when Ecosia/Bing/etc. send
/// traffic — over a configurable window. The '(direct)' bucket is broken down
/// by UA class plus a short-lived raw sample, to tell dark-social/bookmark
/// traffic apart from scripts that slip past the bot filter.
class AnalyticsTab extends ConsumerStatefulWidget {
  const AnalyticsTab({super.key});

  @override
  ConsumerState<AnalyticsTab> createState() => _AnalyticsTabState();
}

class _AnalyticsTabState extends ConsumerState<AnalyticsTab> {
  Map<String, dynamic>? _data;
  bool _loading = true;
  String? _error;
  int _days = 14;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) {
      setState(() {
        _loading = false;
        _error = 'Wallet not connected';
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await ref
          .read(adminApiProvider)
          .fetchAnalytics(walletAddress: wallet, days: _days);
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final data = _data;

    return PageScroll(
      refresh: _load,
      children: [
          Row(
            children: [
              Text('Traffic', style: theme.textTheme.titleLarge),
              const Spacer(),
              DropdownButton<int>(
                value: _days,
                items: const [
                  DropdownMenuItem(value: 7, child: Text('Last 7 days')),
                  DropdownMenuItem(value: 14, child: Text('Last 14 days')),
                  DropdownMenuItem(value: 30, child: Text('Last 30 days')),
                ],
                onChanged: (v) {
                  if (v != null) {
                    setState(() => _days = v);
                    _load();
                  }
                },
              ),
              const SizedBox(width: 8),
              IconButton(
                tooltip: 'Refresh',
                icon: const Icon(Icons.refresh),
                onPressed: _loading ? null : _load,
              ),
            ],
          ),
          const SizedBox(height: 8),
          LoadingStrip(visible: _loading),
          if (_error != null) ErrorBanner(message: _error!),
          if (data != null && data['error'] != null)
            ErrorBanner(message: 'Analytics unavailable (no data yet).'),
          if (data != null && data['error'] == null) ...[
            _totals(theme, colors, data),
            const SizedBox(height: AppLayout.sectionGap),
            _dailyChart(theme, colors, data),
            const SizedBox(height: AppLayout.sectionGap),
            _topPages(theme, colors, data['top_paths']),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Top referrers', data['top_referrers'], 'referrer'),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Direct breakdown (UA class)',
                data['direct_uaclass'], 'ua_class'),
            const SizedBox(height: AppLayout.sectionGap),
            _directSamples(theme, colors, data['direct_samples']),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Top searches', data['top_searches'], 'query'),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Searches with no results',
                data['zero_searches'], 'query'),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Crawlers', data['top_bots'], 'bot'),
            const SizedBox(height: AppLayout.sectionGap),
            _referrerPaths(theme, colors, data['referrer_paths']),
            const SizedBox(height: AppLayout.sectionGap),
            _rankTable(theme, colors, 'Broken / missing URLs (404)',
                data['top_notfound'], 'path', labelKey: 'label'),
          ],
      ],
    );
  }

  /// Which referrer drove which landing page: "source → page  ×views".
  Widget _referrerPaths(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    return _section(
      theme,
      colors,
      'Top source → landing page',
      list.isEmpty
          ? [Text('No data yet', style: theme.textTheme.bodySmall)]
          : [
              for (final r in list)
                _referrerPathRow(theme, colors, r as Map),
            ],
    );
  }

  Widget _referrerPathRow(ThemeData theme, dynamic colors, Map r) {
    final referrer = r['referrer']?.toString() ?? '';
    final label = r['label']?.toString() ?? r['path']?.toString() ?? '';
    final views = '${r['views']}';
    return InkWell(
      onTap: () => _openPath(r['path']?.toString()),
      borderRadius: BorderRadius.circular(6),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 160),
              child: Text(referrer,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: colors.muted, fontWeight: FontWeight.w600)),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6),
              child: Icon(Icons.arrow_forward, size: 13, color: colors.muted),
            ),
            Expanded(
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(color: theme.colorScheme.primary)),
            ),
            const SizedBox(width: 12),
            Text(views,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(fontWeight: FontWeight.w700)),
          ],
        ),
      ),
    );
  }

  /// Recent raw '(direct)' human requests (referer + UA), so the bucket can be
  /// eyeballed: dark-social/bookmark traffic vs scripts with a browser-ish UA.
  Widget _directSamples(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    return _section(
      theme,
      colors,
      'Recent direct requests (7-day sample)',
      list.isEmpty
          ? [Text('No data yet', style: theme.textTheme.bodySmall)]
          : [
              for (final r in list)
                _directSampleRow(theme, colors, r as Map),
            ],
    );
  }

  Widget _directSampleRow(ThemeData theme, dynamic colors, Map r) {
    final path = r['path']?.toString() ?? '';
    final referer = r['referer']?.toString() ?? '';
    final uaClass = r['ua_class']?.toString() ?? '';
    final ua = r['user_agent']?.toString() ?? '';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(path,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyMedium
                        ?.copyWith(fontWeight: FontWeight.w600)),
              ),
              const SizedBox(width: 8),
              _chip(theme, colors, uaClass),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            ua.isEmpty ? '(no user-agent)' : ua,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
          ),
          if (referer.isNotEmpty)
            Text('ref: $referer',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.labelSmall?.copyWith(color: colors.muted)),
        ],
      ),
    );
  }

  Widget _chip(ThemeData theme, dynamic colors, String label) {
    // Browser-shaped classes are "expected" direct; flag the rest as suspect.
    final suspect = label == 'non-browser' || label == 'headless' || label == 'no-ua';
    final color = suspect ? Colors.orange.shade700 : theme.colorScheme.primary;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(label,
          style: theme.textTheme.labelSmall
              ?.copyWith(color: color, fontWeight: FontWeight.w700)),
    );
  }

  Widget _totals(ThemeData theme, dynamic colors, Map<String, dynamic> data) {
    final totals = (data['totals'] as Map?) ?? const {};
    final prev = (data['prev_totals'] as Map?) ?? const {};
    final human = (totals['human'] as num?)?.toInt() ?? 0;
    final bot = (totals['bot'] as num?)?.toInt() ?? 0;
    final humanUnique = (totals['human_unique'] as num?)?.toInt() ?? 0;
    final prevHuman = (prev['human'] as num?)?.toInt() ?? 0;
    final prevBot = (prev['bot'] as num?)?.toInt() ?? 0;
    final prevHumanUnique = (prev['human_unique'] as num?)?.toInt() ?? 0;
    final total = human + bot;
    final share = total == 0 ? 0.0 : human / total;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            _statCard(theme, colors, 'Human views', human, Icons.person_outline,
                delta: _delta(human, prevHuman)),
            const SizedBox(width: 16),
            _statCard(theme, colors, 'Unique visitors', humanUnique,
                Icons.groups_outlined, delta: _delta(humanUnique, prevHumanUnique)),
            const SizedBox(width: 16),
            _statCard(theme, colors, 'Bot views', bot, Icons.smart_toy_outlined,
                delta: _delta(bot, prevBot)),
            const SizedBox(width: 16),
            _statCard(theme, colors, 'Human share', human,
                Icons.pie_chart_outline,
                valueText: '${(share * 100).round()}%'),
          ],
        ),
        const SizedBox(height: 12),
        _splitBar(theme, colors, share),
      ],
    );
  }

  /// Percent change vs the prior period, or null when there's no baseline.
  double? _delta(int current, int previous) {
    if (previous <= 0) return null;
    return (current - previous) / previous * 100;
  }

  Widget _splitBar(ThemeData theme, dynamic colors, double humanShare) {
    final accent = theme.colorScheme.primary;
    return Tooltip(
      message: '${(humanShare * 100).round()}% human · '
          '${(100 - humanShare * 100).round()}% bot',
      child: ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: Row(
          children: [
            Expanded(
              flex: math.max(0, (humanShare * 1000).round()),
              child: Container(height: 6, color: accent),
            ),
            Expanded(
              flex: math.max(0, ((1 - humanShare) * 1000).round()),
              child: Container(height: 6, color: colors.muted.withValues(alpha: 0.35)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _statCard(ThemeData theme, dynamic colors, String label, int value, IconData icon,
      {double? delta, String? valueText}) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: colors.panelBackground,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: colors.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              Icon(icon, size: 16, color: colors.muted),
              const SizedBox(width: 6),
              Text(label, style: theme.textTheme.labelMedium?.copyWith(color: colors.muted)),
            ]),
            const SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(valueText ?? '$value', style: theme.textTheme.headlineSmall),
                if (delta != null) ...[
                  const SizedBox(width: 8),
                  _deltaChip(theme, delta),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _deltaChip(ThemeData theme, double delta) {
    final up = delta >= 0;
    final color = up ? Colors.green.shade600 : Colors.red.shade400;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(up ? Icons.arrow_upward : Icons.arrow_downward, size: 13, color: color),
        Text('${delta.abs().round()}%',
            style: theme.textTheme.labelMedium?.copyWith(
                color: color, fontWeight: FontWeight.w700)),
      ],
    );
  }

  Widget _dailyChart(ThemeData theme, dynamic colors, Map<String, dynamic> data) {
    final daily = (data['daily'] as List?) ?? const [];
    if (daily.isEmpty) {
      return _section(theme, colors, 'By day',
          [Text('No data yet', style: theme.textTheme.bodySmall)]);
    }
    final accent = theme.colorScheme.primary;
    final botColor = colors.muted.withValues(alpha: 0.45);

    double maxY = 1;
    for (final row in daily) {
      final h = ((row as Map)['human'] as num?)?.toDouble() ?? 0;
      final b = (row['bot'] as num?)?.toDouble() ?? 0;
      maxY = math.max(maxY, math.max(h, b));
    }
    final labels = [
      for (final r in daily) ((r as Map)['day']?.toString() ?? '').replaceFirst('${DateTime.now().year}-', '')
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('By day', style: theme.textTheme.titleMedium),
            const Spacer(),
            _legendDot(theme, accent, 'Human'),
            const SizedBox(width: 12),
            _legendDot(theme, botColor, 'Bot'),
          ],
        ),
        const SizedBox(height: 8),
        Container(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          padding: const EdgeInsets.fromLTRB(8, 16, 16, 8),
          child: SizedBox(
            height: 200,
            child: BarChart(
              BarChartData(
                maxY: maxY * 1.15,
                alignment: BarChartAlignment.spaceAround,
                gridData: FlGridData(
                  show: true,
                  drawVerticalLine: false,
                  getDrawingHorizontalLine: (v) =>
                      FlLine(color: colors.border, strokeWidth: 0.6),
                ),
                borderData: FlBorderData(show: false),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: const AxisTitles(
                      sideTitles: SideTitles(showTitles: true, reservedSize: 34)),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 24,
                      getTitlesWidget: (value, meta) {
                        final i = value.toInt();
                        if (i < 0 || i >= labels.length) return const SizedBox.shrink();
                        if (labels.length > 7 && i % 2 != 0) return const SizedBox.shrink();
                        return Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text(labels[i],
                              style: TextStyle(fontSize: 10, color: colors.muted)),
                        );
                      },
                    ),
                  ),
                ),
                barGroups: [
                  for (int i = 0; i < daily.length; i++)
                    BarChartGroupData(
                      x: i,
                      barsSpace: 2,
                      barRods: [
                        BarChartRodData(
                          toY: ((daily[i] as Map)['human'] as num?)?.toDouble() ?? 0,
                          color: accent,
                          width: 6,
                          borderRadius: BorderRadius.circular(2),
                        ),
                        BarChartRodData(
                          toY: ((daily[i] as Map)['bot'] as num?)?.toDouble() ?? 0,
                          color: botColor,
                          width: 6,
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ],
                    ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _legendDot(ThemeData theme, Color color, String label) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 10, height: 10,
            decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(2))),
        const SizedBox(width: 5),
        Text(label, style: theme.textTheme.labelSmall),
      ],
    );
  }

  Widget _rankTable(
      ThemeData theme, dynamic colors, String title, dynamic rows, String key,
      {String? labelKey, bool linkable = false}) {
    final list = (rows as List?) ?? const [];
    return _section(
      theme,
      colors,
      title,
      list.isEmpty
          ? [Text('No data yet', style: theme.textTheme.bodySmall)]
          : [
              for (final r in list)
                _row(
                  theme,
                  (r as Map)[labelKey ?? key]?.toString() ?? '',
                  '${r['views']}',
                  onTap: linkable ? () => _openPath(r[key]?.toString()) : null,
                ),
            ],
    );
  }

  /// Top pages with a human/bot split per page (clickable to open the page).
  Widget _topPages(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    final accent = theme.colorScheme.primary;
    final botColor = colors.muted.withValues(alpha: 0.7);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('Top pages', style: theme.textTheme.titleMedium),
            const Spacer(),
            _legendDot(theme, accent, 'Human'),
            const SizedBox(width: 12),
            _legendDot(theme, botColor, 'Bot'),
          ],
        ),
        const SizedBox(height: 8),
        Container(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Column(
            children: list.isEmpty
                ? [Text('No data yet', style: theme.textTheme.bodySmall)]
                : [
                    for (final r in list)
                      _pageRow(
                        theme,
                        accent,
                        botColor,
                        (r as Map)['label']?.toString() ?? r['path']?.toString() ?? '',
                        (r['human'] as num?)?.toInt() ?? 0,
                        (r['bot'] as num?)?.toInt() ?? 0,
                        () => _openPath(r['path']?.toString()),
                      ),
                  ],
          ),
        ),
      ],
    );
  }

  Widget _pageRow(ThemeData theme, Color accent, Color botColor, String label,
      int human, int bot, VoidCallback onTap) {
    Widget count(int v, Color color) => SizedBox(
          width: 44,
          child: Text('$v',
              textAlign: TextAlign.right,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(fontWeight: FontWeight.w700, color: color)),
        );
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            Expanded(
              child: Text(label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodyMedium?.copyWith(color: accent)),
            ),
            const SizedBox(width: 12),
            count(human, accent),
            count(bot, botColor),
          ],
        ),
      ),
    );
  }

  Future<void> _openPath(String? path) async {
    if (path == null || path.isEmpty) return;
    final uri = Uri.base.resolve(path); // resolve against the site's origin
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  Widget _section(ThemeData theme, dynamic colors, String title, List<Widget> children) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Container(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Column(children: children),
        ),
      ],
    );
  }

  Widget _row(ThemeData theme, String label, String value, {VoidCallback? onTap}) {
    final linkColor = theme.colorScheme.primary;
    final row = Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium?.copyWith(
                    color: onTap != null ? linkColor : null)),
          ),
          const SizedBox(width: 12),
          Text(value, style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w700)),
        ],
      ),
    );
    if (onTap == null) return row;
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: row,
    );
  }
}
