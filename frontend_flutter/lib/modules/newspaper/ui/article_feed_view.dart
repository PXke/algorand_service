import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../sections.dart';
import '../services/news_api.dart';
import '../services/placements_api.dart';
import 'article_card.dart';
import 'feed_placement_card.dart';

/// Shared, paginated story feed with the front-page layout (lead story spans
/// the column, the rest flow two-up on wide screens). Placements are spread
/// through the feed and always span full width.
///
/// Pass [serviceId] to filter server-side by publisher, or [section] to filter
/// client-side by editorial section.
class ArticleFeedView extends ConsumerStatefulWidget {
  const ArticleFeedView({
    super.key,
    this.serviceId,
    this.section,
    this.header,
    this.emptyTitle,
    this.emptyMessage,
    this.showPlacements = true,
  });

  final String? serviceId;
  final NewsSection? section;
  final Widget? header;
  final String? emptyTitle;
  final String? emptyMessage;
  final bool showPlacements;

  @override
  ConsumerState<ArticleFeedView> createState() => _ArticleFeedViewState();
}

class _ArticleFeedViewState extends ConsumerState<ArticleFeedView> {
  static const int _placementEveryNArticles = 5;

  List<Map<String, dynamic>> _items = const [];
  List<Map<String, dynamic>> _placements = const [];
  String? _error;
  bool _loading = true;
  int? _nextCursor;
  bool _loadingMore = false;
  final _scrollController = ScrollController();

  NewsApi _newsApi() => NewsApi(ref.read(apiClientProvider));

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
    _load();
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    final pos = _scrollController.position;
    if (pos.pixels >= pos.maxScrollExtent - 600 &&
        _nextCursor != null &&
        !_loadingMore &&
        !_loading) {
      _loadMore();
    }
  }

  List<Map<String, dynamic>> _filter(List<Map<String, dynamic>> items) {
    final section = widget.section;
    if (section == null) return items;
    return items.where((item) => sectionMatches(section, tagsOf(item))).toList();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final client = ref.read(apiClientProvider);
      final feedPage =
          await _newsApi().fetchFeedPage(limit: 50, serviceId: widget.serviceId);
      List<Map<String, dynamic>> placements = const [];
      if (widget.showPlacements) {
        try {
          placements = await PlacementsApi(client).fetchPlacements();
        } catch (_) {}
      }
      if (!mounted) return;
      setState(() {
        _items = feedPage.items;
        _nextCursor = feedPage.nextCursor;
        _placements = placements;
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

  Future<void> _loadMore() async {
    final cursor = _nextCursor;
    if (cursor == null) return;
    setState(() => _loadingMore = true);
    try {
      final page = await _newsApi()
          .fetchFeedPage(limit: 50, cursor: cursor, serviceId: widget.serviceId);
      if (!mounted) return;
      setState(() {
        _items = [..._items, ...page.items];
        _nextCursor = page.nextCursor;
        _loadingMore = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _loadingMore = false);
    }
  }

  void _openArticle(Map<String, dynamic> item) {
    final id = item['article_id']?.toString() ?? '';
    if (id.isEmpty) return;
    context.go('/news/articles/$id');
  }

  @override
  Widget build(BuildContext context) {
    final visible = _filter(_items);

    return PageScroll(
      controller: _scrollController,
      refresh: _load,
      children: [
        if (widget.header != null) widget.header!,
        PageContent(
          child: LayoutBuilder(
            builder: (context, constraints) {
              final twoCol = constraints.maxWidth >= 700;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const SizedBox(height: AppLayout.sectionGap),
                  LoadingStrip(visible: _loading),
                  if (_error != null) ErrorBanner(message: _error!),
                  if (!_loading && visible.isEmpty)
                    EmptyState(
                      title: widget.emptyTitle ?? '',
                      message: widget.emptyMessage ?? '',
                      icon: Icons.article_outlined,
                    ),
                  ..._buildFeedEntries(visible, twoCol: twoCol),
                ],
              );
            },
          ),
        ),
      ],
    );
  }

  List<Widget> _buildFeedEntries(
    List<Map<String, dynamic>> articles, {
    required bool twoCol,
  }) {
    final entries = <Widget>[];
    var placementIdx = 0;
    final pendingRow = <Widget>[];

    void flushRow() {
      if (pendingRow.isEmpty) return;
      entries.add(
        FadeIn(
          delay: staggerDelay(entries.length),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(child: pendingRow[0]),
                  const SizedBox(width: 16),
                  Expanded(
                    child: pendingRow.length > 1
                        ? pendingRow[1]
                        : const SizedBox.shrink(),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
      pendingRow.clear();
    }

    for (var i = 0; i < articles.length; i++) {
      final item = articles[i];
      final card = ArticleCard(
        item: item,
        hero: i == 0,
        compact: twoCol && i > 0,
        onTap: () => _openArticle(item),
      );
      if (i == 0 || !twoCol) {
        entries.add(
          FadeIn(
            delay: staggerDelay(entries.length),
            child: Padding(padding: const EdgeInsets.only(bottom: 16), child: card),
          ),
        );
      } else {
        pendingRow.add(card);
        if (pendingRow.length == 2) flushRow();
      }
      final shouldPlaceAd =
          (i + 1) % _placementEveryNArticles == 0 && _placements.isNotEmpty;
      if (shouldPlaceAd) {
        flushRow();
        final placement = _placements[placementIdx % _placements.length];
        placementIdx++;
        entries.add(
          FadeIn(
            delay: staggerDelay(entries.length),
            child: Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: FeedPlacementCard(placement: placement),
            ),
          ),
        );
      }
    }
    flushRow();
    return entries;
  }
}
