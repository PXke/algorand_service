import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/footer_scaffold.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';
import '../sections.dart';
import '../services/news_api.dart';

/// The paper's taxonomy as a ledger. Tags are the writer's own labels —
/// richer and more current than any fixed sections — so this page is the
/// honest index of what the newsroom covers: topics ranked by coverage,
/// set in the same hairline-rule column system as every other listing
/// (the earlier word cloud read as a different product).
class TopicsPage extends ConsumerStatefulWidget {
  const TopicsPage({super.key});

  @override
  ConsumerState<TopicsPage> createState() => _TopicsPageState();
}

class _TopicsPageState extends ConsumerState<TopicsPage> {
  TagStats? _stats;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final stats = await NewsApi(ref.read(apiClientProvider)).fetchTagStats();
      if (!mounted) return;
      setState(() {
        _stats = stats;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = apiErrorMessage(e);
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return FooterScaffold(
      onRefresh: _load,
      content: Padding(
        padding: responsivePagePadding(context),
        child: PageContent(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              PageHeader(
                title: l10n.topicsTitle,
                subtitle: l10n.topicsLead,
              ),
              LoadingStrip(visible: _loading),
              if (_error != null) ErrorBanner(message: _error!),
              if (!_loading && _error == null)
                FadeIn(child: _TopicLedger(stats: _stats)),
              const SizedBox(height: AppLayout.sectionGap),
            ],
          ),
        ),
      ),
    );
  }
}

class _TopicLedger extends StatelessWidget {
  const _TopicLedger({required this.stats});

  final TagStats? stats;

  /// Reliability policy (mirrors the backend's sitemap/topic policy): a tag
  /// earns an entry only when the writers used it at least twice — singletons
  /// are labeling noise — and when it isn't boilerplate on half the corpus.
  static const int _minCount = 2;
  static const int _maxEntries = 60;

  List<_TopicEntry> _entries() {
    final s = stats;
    if (s == null) return const [];
    final entries = <_TopicEntry>[];
    for (final raw in s.tags) {
      final tag = raw['tag']?.toString() ?? '';
      final count = raw['count'] is int ? raw['count'] as int : 0;
      final views = raw['views'] is int ? raw['views'] as int : 0;
      if (tag.isEmpty || count < _minCount) continue;
      if (s.articleCount > 4 && count * 2 >= s.articleCount) continue;
      entries.add(_TopicEntry(tag: tag, count: count, views: views));
      if (entries.length >= _maxEntries) break;
    }
    return entries;
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final entries = _entries();
    if (entries.isEmpty) {
      return EmptyState(
        title: l10n.sectionEmptyTitle,
        message: l10n.sectionEmptyMessage,
        icon: Icons.tag,
      );
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 760
            ? 3
            : constraints.maxWidth >= 500
                ? 2
                : 1;
        // Column-major: the ledger reads top-to-bottom like a printed index.
        final perColumn = (entries.length + columns - 1) ~/ columns;
        final columnSlices = [
          for (var c = 0; c < columns; c++)
            entries.sublist(
              c * perColumn,
              ((c + 1) * perColumn).clamp(0, entries.length),
            ),
        ];
        return IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var c = 0; c < columnSlices.length; c++) ...[
                if (c > 0)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 20),
                    child: VerticalDivider(
                        width: 1, color: context.appColors.border),
                  ),
                Expanded(
                  child: Column(
                    children: [
                      for (var i = 0; i < columnSlices[c].length; i++)
                        _TopicRow(
                          entry: columnSlices[c][i],
                          first: i == 0,
                        ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        );
      },
    );
  }
}

class _TopicEntry {
  const _TopicEntry({required this.tag, required this.count, required this.views});
  final String tag;
  final int count;
  final int views;
}

class _TopicRow extends StatefulWidget {
  const _TopicRow({required this.entry, required this.first});

  final _TopicEntry entry;
  final bool first;

  @override
  State<_TopicRow> createState() => _TopicRowState();
}

class _TopicRowState extends State<_TopicRow> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final l10n = context.l10n;
    final entry = widget.entry;

    // Coverage tier sets the type size — the ledger's only flourish.
    final fontSize = entry.count >= 8
        ? 24.0
        : entry.count >= 4
            ? 20.0
            : 17.0;

    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: () =>
            context.go('/topic/${Uri.encodeComponent(entry.tag)}'),
        behavior: HitTestBehavior.opaque,
        child: Container(
          decoration: widget.first
              ? null
              : BoxDecoration(
                  border: Border(top: BorderSide(color: colors.border)),
                ),
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AnimatedDefaultTextStyle(
                duration: const Duration(milliseconds: 120),
                style: theme.textTheme.titleLarge!.copyWith(
                  fontSize: fontSize,
                  height: 1.15,
                  color: _hovered ? theme.colorScheme.primary : null,
                ),
                child: Text(displayTagLabel(entry.tag)),
              ),
              const SizedBox(height: 4),
              Text(
                '${l10n.storiesCount(entry.count)} · ${l10n.readsCount(entry.views)}',
                style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
