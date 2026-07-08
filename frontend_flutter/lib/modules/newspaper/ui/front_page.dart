import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/util/ssr_feed_payload.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/l10n/locale_provider.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/footer_scaffold.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../services/news_api.dart';
import '../services/placements_api.dart';
import 'article_card.dart';
import 'feed_placement_card.dart';

/// The paper's front page: a section rail, a lead story, a top-stories grid,
/// the latest file, and the standing footer. This is the app's landing route.
class FrontPage extends ConsumerStatefulWidget {
  const FrontPage({super.key});

  @override
  ConsumerState<FrontPage> createState() => _FrontPageState();
}

class _FrontPageState extends ConsumerState<FrontPage> {
  List<Map<String, dynamic>> _items = const [];
  Map<String, dynamic>? _placement;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    final boot = readSsrFeedItems();
    if (boot != null && boot.isNotEmpty) {
      _items = boot;
      _loading = false;
      WidgetsBinding.instance.addPostFrameCallback((_) => _refreshInBackground());
    } else {
      _load();
    }
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadPlacementsDeferred());
  }

  void _loadPlacementsDeferred() {
    unawaited(
      Future<void>.delayed(const Duration(seconds: 8), _loadPlacements),
    );
  }

  /// Re-fetch after painting SSR-hydrated content so the feed stays fresh.
  Future<void> _refreshInBackground() async {
    await Future<void>.delayed(const Duration(seconds: 5));
    if (!mounted) return;
    await _load(silent: true);
    if (!mounted) return;
    await _loadPlacements();
  }

  Future<void> _loadPlacements() async {
    try {
      final placements = await PlacementsApi(ref.read(apiClientProvider)).fetchPlacements();
      if (!mounted) return;
      if (placements.isNotEmpty) {
        setState(() => _placement = placements.first);
      }
    } catch (_) {}
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final lang = contentLanguageCode(ref, context);
      final client = ref.read(apiClientProvider);
      final page = await NewsApi(client).fetchFeedPage(limit: 30, lang: lang);
      if (!mounted) return;
      setState(() {
        _items = page.items;
        _loading = false;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      if (silent && _items.isNotEmpty) return;
      setState(() {
        _error = apiErrorMessage(e);
        _loading = false;
      });
    }
  }

  void _open(Map<String, dynamic> item) {
    final id = item['article_id']?.toString() ?? '';
    if (id.isEmpty) return;
    context.go('/news/articles/$id');
  }

  @override
  Widget build(BuildContext context) {
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
              LoadingStrip(visible: _loading),
              if (_error != null) ErrorBanner(message: _error!),
              ..._buildBody(context),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _buildBody(BuildContext context) {
    if (_items.isEmpty) return const [];
    final l10n = context.l10n;
    final lead = _items.first;
    final secondary = _items.skip(1).take(4).toList();
    final rest = _items.skip(5).toList();
    final twoCol = MediaQuery.sizeOf(context).width >= 700;

    return [
      FadeIn(child: ArticleCard(item: lead, hero: true, onTap: () => _open(lead))),
      if (secondary.isNotEmpty) ...[
        const SizedBox(height: AppLayout.sectionGap),
        _SectionLabel(text: l10n.frontPageTopStories, icon: Icons.star_outline),
        const SizedBox(height: AppLayout.itemGap),
        _Grid(items: secondary, twoCol: twoCol, onOpen: _open),
      ],
      if (_placement != null) ...[
        const SizedBox(height: AppLayout.sectionGap),
        FadeIn(child: FeedPlacementCard(placement: _placement!)),
      ],
      if (rest.isNotEmpty) ...[
        const SizedBox(height: AppLayout.sectionGap),
        _SectionLabel(text: l10n.frontPageLatest, icon: Icons.bolt_outlined),
        const SizedBox(height: AppLayout.itemGap),
        _Grid(items: rest, twoCol: twoCol, onOpen: _open),
      ],
      const SizedBox(height: AppLayout.sectionGap),
      Center(
        child: OutlinedButton.icon(
          onPressed: () => context.go('/news'),
          icon: const Icon(Icons.east, size: 18),
          label: Text(l10n.frontPageMore),
        ),
      ),
    ];
  }
}

/// Two-column grid of compact story cards (single column on narrow screens).
class _Grid extends StatelessWidget {
  const _Grid({required this.items, required this.twoCol, required this.onOpen});

  final List<Map<String, dynamic>> items;
  final bool twoCol;
  final void Function(Map<String, dynamic>) onOpen;

  @override
  Widget build(BuildContext context) {
    if (!twoCol) {
      return Column(
        children: [
          for (var i = 0; i < items.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: FadeIn(
                delay: staggerDelay(i),
                child: ArticleCard(item: items[i], onTap: () => onOpen(items[i])),
              ),
            ),
        ],
      );
    }
    final rows = <Widget>[];
    for (var i = 0; i < items.length; i += 2) {
      final left = items[i];
      final right = i + 1 < items.length ? items[i + 1] : null;
      rows.add(
        FadeIn(
          delay: staggerDelay(i ~/ 2),
          child: Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: kIsWeb
                ? Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: ArticleCard(
                          item: left,
                          compact: true,
                          onTap: () => onOpen(left),
                        ),
                      ),
                      const SizedBox(width: 16),
                      Expanded(
                        child: right == null
                            ? const SizedBox.shrink()
                            : ArticleCard(
                                item: right,
                                compact: true,
                                onTap: () => onOpen(right),
                              ),
                      ),
                    ],
                  )
                : IntrinsicHeight(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Expanded(
                          child: ArticleCard(
                            item: left,
                            compact: true,
                            onTap: () => onOpen(left),
                          ),
                        ),
                        const SizedBox(width: 16),
                        Expanded(
                          child: right == null
                              ? const SizedBox.shrink()
                              : ArticleCard(
                                  item: right,
                                  compact: true,
                                  onTap: () => onOpen(right),
                                ),
                        ),
                      ],
                    ),
                  ),
          ),
        ),
      );
    }
    return Column(children: rows);
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel({required this.text, this.icon});

  final String text;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Row(
      children: [
        if (icon != null) ...[
          Icon(icon, size: 15, color: colors.accent),
          const SizedBox(width: 7),
        ],
        Text(
          text.toUpperCase(),
          style: theme.textTheme.labelSmall?.copyWith(
            color: colors.subtle,
            letterSpacing: 0.9,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(width: 12),
        Expanded(child: Divider(height: 1, color: colors.border)),
      ],
    );
  }
}
