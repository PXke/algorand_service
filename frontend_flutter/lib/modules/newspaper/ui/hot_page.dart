import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/util/ssr_feed_payload.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/l10n/locale_provider.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/footer_scaffold.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/page_header.dart';
import '../services/news_api.dart';
import 'story_row.dart';

/// The most-read file: recent stories ranked by read tally. Rank is the
/// information here, so the page is a numbered ledger, not a card grid.
class HotPage extends ConsumerStatefulWidget {
  const HotPage({super.key});

  @override
  ConsumerState<HotPage> createState() => _HotPageState();
}

class _HotPageState extends ConsumerState<HotPage> {
  List<Map<String, dynamic>> _items = const [];
  String? _error;
  bool _loading = true;

  /// 'hot' = read velocity (views/day since publish); 'top' = lifetime reads.
  String _rank = 'hot';

  @override
  void initState() {
    super.initState();
    // SSR boot payload is the default 'hot' ranking only.
    final boot = readSsrFeedItems();
    if (boot != null && boot.isNotEmpty) {
      _items = boot;
      _loading = false;
      WidgetsBinding.instance.addPostFrameCallback((_) => _refreshInBackground());
    } else {
      _load();
    }
  }

  Future<void> _refreshInBackground() async {
    try {
      final lang = contentLanguageCode(ref, context);
      final items = await NewsApi(ref.read(apiClientProvider))
          .fetchHot(limit: 30, lang: lang, rank: _rank);
      if (!mounted) return;
      setState(() => _items = items);
    } catch (_) {}
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final lang = contentLanguageCode(ref, context);
      final items = await NewsApi(ref.read(apiClientProvider))
          .fetchHot(limit: 30, lang: lang, rank: _rank);
      if (!mounted) return;
      setState(() {
        _items = items;
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

  void _setRank(String rank) {
    if (rank == _rank) return;
    setState(() => _rank = rank);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    ref.listen(localeProvider, (previous, next) {
      if (previous != next) _load();
    });
    return FooterScaffold(
      onRefresh: _load,
      content: Padding(
        padding: responsivePagePadding(context),
        child: PageContent(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              PageHeader(title: l10n.hotTitle, subtitle: l10n.hotLead),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: SegmentedButton<String>(
                  segments: [
                    ButtonSegment(value: 'hot', label: Text(l10n.hotTabHot)),
                    ButtonSegment(value: 'top', label: Text(l10n.hotTabAllTime)),
                  ],
                  selected: {_rank},
                  showSelectedIcon: false,
                  onSelectionChanged: (selection) => _setRank(selection.first),
                ),
              ),
              const SizedBox(height: AppLayout.itemGap),
              LoadingStrip(visible: _loading),
              if (_error != null) ErrorBanner(message: _error!),
              if (!_loading && _error == null && _items.isEmpty)
                EmptyState(
                  title: l10n.sectionEmptyTitle,
                  message: l10n.sectionEmptyMessage,
                  icon: Icons.local_fire_department_outlined,
                ),
              for (var i = 0; i < _items.length; i++)
                FadeIn(
                  delay: staggerDelay(i),
                  child: StoryRow(
                    rank: i + 1,
                    item: _items[i],
                    first: i == 0,
                    onTap: () {
                      final id = _items[i]['article_id']?.toString() ?? '';
                      if (id.isNotEmpty) context.go('/news/articles/$id');
                    },
                  ),
                ),
              const SizedBox(height: AppLayout.sectionGap),
            ],
          ),
        ),
      ),
    );
  }
}
