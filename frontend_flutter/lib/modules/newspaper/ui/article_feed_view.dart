import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/util/ssr_feed_payload.dart';
import '../../../core/l10n/locale_provider.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../services/news_api.dart';
import '../services/placements_api.dart';
import 'article_card.dart';
import 'story_row.dart';
import 'feed_placement_card.dart';

/// Shared, paginated story feed with the front-page layout (lead story spans
/// the column, the rest flow two-up on wide screens). Placements are spread
/// through the feed and always span full width.
///
/// Pass [serviceId] to filter server-side by publisher, or [tag] to filter
/// server-side by writer tag.
class ArticleFeedView extends ConsumerStatefulWidget {
  const ArticleFeedView({
    super.key,
    this.serviceId,
    this.tag,
    this.header,
    this.emptyTitle,
    this.emptyMessage,
    this.showPlacements = true,
  });

  final String? serviceId;
  final String? tag;
  final Widget? header;
  final String? emptyTitle;
  final String? emptyMessage;
  final bool showPlacements;

  @override
  ConsumerState<ArticleFeedView> createState() => _ArticleFeedViewState();
}

class _ArticleFeedViewState extends ConsumerState<ArticleFeedView> {
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
    final boot = (widget.serviceId == null || widget.serviceId!.isEmpty)
        ? readSsrFeedItems()
        : null;
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
      final page = await _newsApi().fetchFeedPage(
        limit: 30,
        tag: widget.tag,
        serviceId: widget.serviceId,
        lang: contentLanguageCode(ref, context),
      );
      if (!mounted) return;
      setState(() {
        _items = page.items;
        _nextCursor = page.nextCursor;
      });
    } catch (_) {}
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

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final lang = contentLanguageCode(ref, context);
      final client = ref.read(apiClientProvider);
      final feedPage = await _newsApi().fetchFeedPage(
          limit: 50, serviceId: widget.serviceId, tag: widget.tag, lang: lang);
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
      final lang = contentLanguageCode(ref, context);
      final page = await _newsApi().fetchFeedPage(
          limit: 50,
          cursor: cursor,
          serviceId: widget.serviceId,
          tag: widget.tag,
          lang: lang);
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
    ref.listen(localeProvider, (previous, next) {
      if (previous != next) _load();
    });
    final visible = _items;

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

  /// Lead package + the flat story file, with placements between chunks.
  /// Same visual system as the front page: hairline-separated [StoryRow]s in
  /// two balanced columns — no card tiles.
  List<Widget> _buildFeedEntries(
    List<Map<String, dynamic>> articles, {
    required bool twoCol,
  }) {
    if (articles.isEmpty) return const [];
    final entries = <Widget>[
      FadeIn(
        child: ArticleCard(
          item: articles.first,
          hero: true,
          onTap: () => _openArticle(articles.first),
        ),
      ),
    ];
    final rest = articles.sublist(1);
    var placementIdx = 0;
    const chunkSize = 8;
    for (var start = 0; start < rest.length; start += chunkSize) {
      final end =
          (start + chunkSize < rest.length) ? start + chunkSize : rest.length;
      final chunk = rest.sublist(start, end);
      entries.add(const SizedBox(height: AppLayout.itemGap));
      entries.add(Divider(height: 1, color: context.appColors.border));
      entries.add(
        FadeIn(
          delay: staggerDelay(start ~/ chunkSize),
          child: StoryRowGrid(items: chunk, twoCol: twoCol, onOpen: _openArticle),
        ),
      );
      if (_placements.isNotEmpty && end < rest.length) {
        final placement = _placements[placementIdx % _placements.length];
        placementIdx++;
        entries.add(const SizedBox(height: AppLayout.itemGap));
        entries.add(
          FadeIn(child: FeedPlacementCard(placement: placement)),
        );
      }
    }
    return entries;
  }
}
