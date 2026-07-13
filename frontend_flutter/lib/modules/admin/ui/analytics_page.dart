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
import '../../../core/providers/session_providers.dart';

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
  String _group = 'Overview';

  static const List<String> _groups = [
    'Overview',
    'Acquisition',
    'Content',
    'Audience',
    'Crawlers',
  ];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
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
            _alertsStrip(theme, colors, data['alerts']),
            _totals(theme, colors, data),
            const SizedBox(height: AppLayout.sectionGap),
            _groupSelector(theme, colors),
            const SizedBox(height: AppLayout.sectionGap),
            ..._groupChildren(theme, colors, data),
          ],
      ],
    );
  }

  /// Segmented selector that switches which group of sections is shown, so the
  /// page stays scannable instead of one long scroll. Horizontally scrollable so
  /// it never overflows on a narrow admin pane.
  Widget _groupSelector(ThemeData theme, dynamic colors) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SegmentedButton<String>(
        segments: [
          for (final g in _groups)
            ButtonSegment(value: g, label: Text(g)),
        ],
        selected: {_group},
        showSelectedIcon: false,
        onSelectionChanged: (s) => setState(() => _group = s.first),
      ),
    );
  }

  /// The sections for the active group, interleaved with section gaps.
  List<Widget> _groupChildren(
      ThemeData theme, dynamic colors, Map<String, dynamic> data) {
    final List<Widget> sections;
    switch (_group) {
      case 'Acquisition':
        sections = [
          _donutRow(theme, colors, [
            _donutCard(theme, colors, 'Referrer channels',
                data['referrer_categories'], 'category'),
          ]),
          _rankTable(theme, colors, 'Campaigns (utm / ref tags)',
              data['campaigns'], 'campaign'),
          _rankTable(theme, colors, 'Top referrers', data['top_referrers'], 'referrer'),
          _rankTable(theme, colors, 'Top referrers (full URL)',
              data['top_referrer_urls'], 'referrer_url', linkExternal: true),
          _referrerPaths(theme, colors, data['referrer_paths']),
          _rankTable(theme, colors, 'Direct breakdown (UA class)',
              data['direct_uaclass'], 'ua_class'),
          _directSamples(theme, colors, data['direct_samples']),
        ];
        break;
      case 'Content':
        sections = [
          _topPages(theme, colors, data['top_paths']),
          _rankTable(theme, colors, 'Sections', data['sections'], 'section'),
          _editorialScorecard(theme, colors, data['articles']),
          _rankTable(theme, colors, 'Top searches', data['top_searches'], 'query'),
          _rankTable(theme, colors, 'Searches with no results',
              data['zero_searches'], 'query'),
          _rankTable(theme, colors, 'Broken / missing URLs (404)',
              data['top_notfound'], 'path', labelKey: 'label'),
        ];
        break;
      case 'Audience':
        sections = [
          _donutRow(theme, colors, [
            _donutCard(theme, colors, 'Devices', data['device'], 'device'),
            _donutCard(theme, colors, 'Browsers', data['browser'], 'browser'),
            _donutCard(theme, colors, 'Languages', data['languages'], 'lang'),
          ]),
          _sessionsChart(theme, colors, data['sessions_daily']),
          _hourChart(theme, colors, data['hours']),
          _countries(theme, colors, data['geo']),
        ];
        break;
      case 'Crawlers':
        sections = [
          _aiCrawler(theme, colors, data['ai_crawler']),
          _rankTable(theme, colors, 'All crawlers', data['top_bots'], 'bot'),
        ];
        break;
      case 'Overview':
      default:
        sections = [
          _dailyChart(theme, colors, data),
          _donutRow(theme, colors, [
            _donutCard(theme, colors, 'Referrer channels',
                data['referrer_categories'], 'category'),
            _donutCard(theme, colors, 'Devices', data['device'], 'device'),
          ]),
        ];
        break;
    }
    final out = <Widget>[];
    for (var i = 0; i < sections.length; i++) {
      out.add(sections[i]);
      if (i != sections.length - 1) {
        out.add(const SizedBox(height: AppLayout.sectionGap));
      }
    }
    return out;
  }

  Widget _donutRow(ThemeData theme, dynamic colors, List<Widget> donuts) {
    return Wrap(spacing: 16, runSpacing: 16, children: donuts);
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

    final sessions = (data['sessions'] as Map?) ?? const {};
    final sessTotal = (sessions['total'] as num?)?.toInt() ?? 0;
    final returningRate = (sessions['returning_rate'] as num?)?.toDouble() ?? 0.0;
    final pagesPerVisit = (sessions['pages_per_visit'] as num?)?.toDouble() ?? 0.0;
    // Sessions that never got a confirmed 2nd hit — a bot-likelihood signal
    // (UA denylist alone misses a scraper spoofing a browser UA), not a hard
    // filter: plenty of genuine one-and-done readers land in here too.
    final bounceRate = (sessions['bounce_rate'] as num?)?.toDouble() ?? 0.0;
    final prevSessions = (prev['sessions'] as num?)?.toInt() ?? 0;
    final ai = (data['ai_crawler'] as Map?) ?? const {};
    final aiShare = (ai['share_of_bots'] as num?)?.toDouble() ?? 0.0;

    return LayoutBuilder(builder: (context, constraints) {
      // Reflow the stat cards instead of overflowing on narrow widths: aim for 4
      // across on wide screens, 2 across when cramped.
      final perRow = constraints.maxWidth < 720 ? 2 : 4;
      final cardWidth =
          (constraints.maxWidth - 16 * (perRow - 1)) / perRow;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 16,
            runSpacing: 16,
            children: [
              _statCard(theme, colors, 'Human views', human, Icons.person_outline,
                  width: cardWidth, delta: _delta(human, prevHuman)),
              _statCard(theme, colors, 'Unique visitors', humanUnique,
                  Icons.groups_outlined,
                  width: cardWidth, delta: _delta(humanUnique, prevHumanUnique)),
              _statCard(theme, colors, 'Visits', sessTotal, Icons.login_outlined,
                  width: cardWidth, delta: _delta(sessTotal, prevSessions)),
              _statCard(theme, colors, 'Returning', sessTotal,
                  Icons.replay_outlined,
                  width: cardWidth, valueText: '${(returningRate * 100).round()}%'),
              _statCard(theme, colors, 'Pages / visit', sessTotal,
                  Icons.auto_stories_outlined,
                  width: cardWidth,
                  valueText: pagesPerVisit.toStringAsFixed(1)),
              _statCard(theme, colors, 'Likely single-hit', sessTotal,
                  Icons.help_outline,
                  width: cardWidth, valueText: '${(bounceRate * 100).round()}%'),
              _statCard(theme, colors, 'Bot views', bot, Icons.smart_toy_outlined,
                  width: cardWidth, delta: _delta(bot, prevBot)),
              _statCard(theme, colors, 'AI-crawler share', bot,
                  Icons.auto_awesome_outlined,
                  width: cardWidth, valueText: '${(aiShare * 100).round()}%'),
              _statCard(theme, colors, 'Human share', human,
                  Icons.pie_chart_outline,
                  width: cardWidth, valueText: '${(share * 100).round()}%'),
            ],
          ),
          const SizedBox(height: 12),
          _splitBar(theme, colors, share),
        ],
      );
    });
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
      {double? delta, String? valueText, double? width}) {
    return SizedBox(
      width: width,
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

  // ── Segmentation donuts (device / referrer channel / browser) ──────────────

  /// A categorical palette for the donut/segment charts, cycled by index.
  List<Color> _palette(ThemeData theme) => [
        theme.colorScheme.primary,
        Colors.teal.shade400,
        Colors.orange.shade400,
        Colors.purple.shade300,
        Colors.blue.shade400,
        Colors.pink.shade300,
        Colors.green.shade400,
        Colors.amber.shade600,
        Colors.indigo.shade300,
        Colors.brown.shade400,
      ];

  /// A fixed-width card with a donut + legend for one categorical breakdown.
  /// `rows` is a ranked list of `{<keyName>, views}`.
  Widget _donutCard(
      ThemeData theme, dynamic colors, String title, dynamic rows, String keyName) {
    final list = (rows as List?) ?? const [];
    final total = list.fold<int>(
        0, (sum, r) => sum + (((r as Map)['views'] as num?)?.toInt() ?? 0));
    final palette = _palette(theme);
    return SizedBox(
      width: 320,
      child: Column(
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
            padding: const EdgeInsets.all(16),
            child: total == 0
                ? Text('No data yet', style: theme.textTheme.bodySmall)
                : Row(
                    children: [
                      SizedBox(
                        width: 110,
                        height: 110,
                        child: PieChart(
                          PieChartData(
                            sectionsSpace: 2,
                            centerSpaceRadius: 30,
                            sections: [
                              for (int i = 0; i < list.length; i++)
                                PieChartSectionData(
                                  value: (((list[i] as Map)['views'] as num?)
                                              ?.toDouble() ??
                                          0),
                                  color: palette[i % palette.length],
                                  radius: 22,
                                  showTitle: false,
                                ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            for (int i = 0; i < list.length; i++)
                              _donutLegendRow(
                                theme,
                                colors,
                                palette[i % palette.length],
                                (list[i] as Map)[keyName]?.toString() ?? '',
                                ((list[i] as Map)['views'] as num?)?.toInt() ?? 0,
                                total,
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  Widget _donutLegendRow(ThemeData theme, dynamic colors, Color color,
      String label, int value, int total) {
    final pct = total == 0 ? 0 : (value / total * 100).round();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Container(
            width: 9,
            height: 9,
            decoration:
                BoxDecoration(color: color, borderRadius: BorderRadius.circular(2)),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall),
          ),
          const SizedBox(width: 6),
          Text('$pct%',
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: colors.muted, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  /// Hour-of-day distribution (UTC) — 24 bars showing when readers show up.
  Widget _hourChart(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    final hasData = list.any((r) => (((r as Map)['views'] as num?)?.toInt() ?? 0) > 0);
    if (!hasData) {
      return _section(theme, colors, 'By hour of day (UTC)',
          [Text('No data yet', style: theme.textTheme.bodySmall)]);
    }
    final accent = theme.colorScheme.primary;
    double maxY = 1;
    for (final r in list) {
      maxY = math.max(maxY, ((r as Map)['views'] as num?)?.toDouble() ?? 0);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('By hour of day (UTC)', style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Container(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          padding: const EdgeInsets.fromLTRB(8, 16, 16, 8),
          child: SizedBox(
            height: 180,
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
                  topTitles:
                      const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles:
                      const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: const AxisTitles(
                      sideTitles: SideTitles(showTitles: true, reservedSize: 34)),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 22,
                      getTitlesWidget: (value, meta) {
                        final h = value.toInt();
                        if (h % 6 != 0) return const SizedBox.shrink();
                        return Padding(
                          padding: const EdgeInsets.only(top: 6),
                          child: Text('${h}h',
                              style: TextStyle(fontSize: 10, color: colors.muted)),
                        );
                      },
                    ),
                  ),
                ),
                barGroups: [
                  for (final r in list)
                    BarChartGroupData(
                      x: ((r as Map)['hour'] as num?)?.toInt() ?? 0,
                      barRods: [
                        BarChartRodData(
                          toY: (r['views'] as num?)?.toDouble() ?? 0,
                          color: accent,
                          width: 7,
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

  // ── New-vs-returning sessions (Audience) ───────────────────────────────────

  Widget _sessionsChart(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    final hasData = list.any((r) =>
        (((r as Map)['new'] as num?)?.toInt() ?? 0) +
            ((r['returning'] as num?)?.toInt() ?? 0) >
        0);
    if (!hasData) {
      return _section(theme, colors, 'Visits — new vs returning',
          [Text('No data yet', style: theme.textTheme.bodySmall)]);
    }
    final accent = theme.colorScheme.primary;
    final returningColor = Colors.teal.shade400;
    double maxY = 1;
    for (final r in list) {
      final v = (((r as Map)['new'] as num?)?.toDouble() ?? 0) +
          ((r['returning'] as num?)?.toDouble() ?? 0);
      maxY = math.max(maxY, v);
    }
    final labels = [
      for (final r in list)
        ((r as Map)['day']?.toString() ?? '')
            .replaceFirst('${DateTime.now().year}-', '')
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text('Visits — new vs returning', style: theme.textTheme.titleMedium),
            const Spacer(),
            _legendDot(theme, accent, 'New'),
            const SizedBox(width: 12),
            _legendDot(theme, returningColor, 'Returning'),
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
            height: 180,
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
                  topTitles:
                      const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  rightTitles:
                      const AxisTitles(sideTitles: SideTitles(showTitles: false)),
                  leftTitles: const AxisTitles(
                      sideTitles: SideTitles(showTitles: true, reservedSize: 34)),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 24,
                      getTitlesWidget: (value, meta) {
                        final i = value.toInt();
                        if (i < 0 || i >= labels.length) {
                          return const SizedBox.shrink();
                        }
                        if (labels.length > 7 && i % 2 != 0) {
                          return const SizedBox.shrink();
                        }
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
                  for (int i = 0; i < list.length; i++)
                    BarChartGroupData(
                      x: i,
                      barRods: [
                        BarChartRodData(
                          toY: (((list[i] as Map)['new'] as num?)?.toDouble() ?? 0) +
                              (((list[i] as Map)['returning'] as num?)?.toDouble() ??
                                  0),
                          width: 9,
                          borderRadius: BorderRadius.circular(2),
                          rodStackItems: [
                            BarChartRodStackItem(
                              0,
                              ((list[i] as Map)['new'] as num?)?.toDouble() ?? 0,
                              accent,
                            ),
                            BarChartRodStackItem(
                              ((list[i] as Map)['new'] as num?)?.toDouble() ?? 0,
                              (((list[i] as Map)['new'] as num?)?.toDouble() ?? 0) +
                                  (((list[i] as Map)['returning'] as num?)
                                          ?.toDouble() ??
                                      0),
                              returningColor,
                            ),
                          ],
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

  // ── AI-crawler visibility (Crawlers) ───────────────────────────────────────

  Widget _aiCrawler(ThemeData theme, dynamic colors, dynamic ai) {
    final m = (ai as Map?) ?? const {};
    final views = (m['views'] as num?)?.toInt() ?? 0;
    final share = (m['share_of_bots'] as num?)?.toDouble() ?? 0.0;
    final daily = (m['daily'] as List?) ?? const [];
    final accent = theme.colorScheme.primary;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('AI crawlers', style: theme.textTheme.titleMedium),
        const SizedBox(height: 8),
        Container(
          decoration: BoxDecoration(
            color: colors.panelBackground,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: colors.border),
          ),
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(children: [
                    Icon(Icons.auto_awesome_outlined, size: 16, color: colors.muted),
                    const SizedBox(width: 6),
                    Text('GPTBot · ClaudeBot · Perplexity · CCBot · Bytespider',
                        style:
                            theme.textTheme.labelSmall?.copyWith(color: colors.muted)),
                  ]),
                  const SizedBox(height: 8),
                  Text('$views', style: theme.textTheme.headlineSmall),
                  Text('${(share * 100).round()}% of bot traffic',
                      style:
                          theme.textTheme.bodySmall?.copyWith(color: colors.muted)),
                ],
              ),
              const SizedBox(width: 20),
              Expanded(
                child: SizedBox(
                  height: 70,
                  child: _sparkline(daily, accent),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  // ── Editorial scorecard (Content) ──────────────────────────────────────────

  Widget _editorialScorecard(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    return _section(
      theme,
      colors,
      'Article performance',
      list.isEmpty
          ? [Text('No data yet', style: theme.textTheme.bodySmall)]
          : [
              for (final r in list) _scorecardRow(theme, colors, r as Map),
            ],
    );
  }

  Widget _scorecardRow(ThemeData theme, dynamic colors, Map r) {
    final label = r['label']?.toString() ?? r['path']?.toString() ?? '';
    final section = r['section']?.toString();
    final age = (r['age_days'] as num?)?.toInt();
    final views = (r['views'] as num?)?.toInt() ?? 0;
    final daily = (r['daily'] as List?) ?? const [];
    final accent = theme.colorScheme.primary;
    final ageText = age == null
        ? null
        : age <= 0
            ? 'today'
            : age == 1
                ? '1d old'
                : '${age}d old';
    return InkWell(
      onTap: () => _openPath(r['path']?.toString()),
      borderRadius: BorderRadius.circular(6),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodyMedium?.copyWith(color: accent)),
                  const SizedBox(height: 2),
                  Row(children: [
                    if (section != null && section.isNotEmpty) ...[
                      Text(section,
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: colors.muted)),
                      const SizedBox(width: 8),
                    ],
                    if (ageText != null)
                      Text(ageText,
                          style: theme.textTheme.labelSmall
                              ?.copyWith(color: colors.muted)),
                  ]),
                ],
              ),
            ),
            const SizedBox(width: 12),
            SizedBox(width: 90, height: 34, child: _sparkline(daily, accent)),
            const SizedBox(width: 12),
            SizedBox(
              width: 50,
              child: Text('$views',
                  textAlign: TextAlign.right,
                  style: theme.textTheme.bodyMedium
                      ?.copyWith(fontWeight: FontWeight.w700)),
            ),
          ],
        ),
      ),
    );
  }

  /// A compact filled line chart of a `[{day, views}]` series, no axes.
  Widget _sparkline(List<dynamic> daily, Color color) {
    if (daily.isEmpty) return const SizedBox.shrink();
    final spots = <FlSpot>[
      for (int i = 0; i < daily.length; i++)
        FlSpot(i.toDouble(),
            ((daily[i] as Map)['views'] as num?)?.toDouble() ?? 0),
    ];
    double maxY = 1;
    for (final s in spots) {
      maxY = math.max(maxY, s.y);
    }
    return LineChart(
      LineChartData(
        minY: 0,
        maxY: maxY * 1.1,
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        lineTouchData: const LineTouchData(enabled: false),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            barWidth: 2,
            color: color,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: true,
              color: color.withValues(alpha: 0.12),
            ),
          ),
        ],
      ),
    );
  }

  // ── Anomaly alerts strip (top of page) ─────────────────────────────────────

  Widget _alertsStrip(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    if (list.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: AppLayout.sectionGap),
      child: Column(
        children: [for (final r in list) _alertChip(theme, r as Map)],
      ),
    );
  }

  Widget _alertChip(ThemeData theme, Map r) {
    final warn = (r['level']?.toString() ?? 'info') == 'warn';
    final color = warn ? Colors.orange.shade800 : theme.colorScheme.primary;
    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.45)),
      ),
      child: Row(
        children: [
          Icon(warn ? Icons.warning_amber_rounded : Icons.info_outline,
              size: 16, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(r['text']?.toString() ?? '',
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: color, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }

  // ── Countries (Audience), with DB-IP attribution ───────────────────────────

  Widget _countries(ThemeData theme, dynamic colors, dynamic rows) {
    final list = (rows as List?) ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _section(
          theme,
          colors,
          'Countries',
          list.isEmpty
              ? [Text('No data yet', style: theme.textTheme.bodySmall)]
              : [
                  for (final r in list)
                    _row(
                      theme,
                      '${_flagEmoji((r as Map)['country']?.toString() ?? '')}  '
                          '${r['country']?.toString() ?? '—'}',
                      '${r['views']}',
                    ),
                ],
        ),
        const SizedBox(height: 6),
        Text('IP geolocation by DB-IP (db-ip.com)',
            style: theme.textTheme.labelSmall?.copyWith(color: colors.muted)),
      ],
    );
  }

  /// Regional-indicator flag emoji for a 2-letter ISO country code.
  String _flagEmoji(String cc) {
    final up = cc.toUpperCase();
    if (up.length != 2) return '🏳️';
    final a = up.codeUnitAt(0), b = up.codeUnitAt(1);
    if (a < 65 || a > 90 || b < 65 || b > 90) return '🏳️';
    return String.fromCharCode(0x1F1E6 + (a - 65)) +
        String.fromCharCode(0x1F1E6 + (b - 65));
  }

  Widget _rankTable(
      ThemeData theme, dynamic colors, String title, dynamic rows, String key,
      {String? labelKey, bool linkable = false, bool linkExternal = false}) {
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
                  onTap: linkExternal
                      ? () => _openExternal(r[key]?.toString())
                      : linkable
                          ? () => _openPath(r[key]?.toString())
                          : null,
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

  /// Open a referrer's full URL (stored scheme-less, e.g. "reddit.com/r/..").
  Future<void> _openExternal(String? url) async {
    if (url == null || url.isEmpty) return;
    final uri = Uri.parse(url.startsWith('http') ? url : 'https://$url');
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
