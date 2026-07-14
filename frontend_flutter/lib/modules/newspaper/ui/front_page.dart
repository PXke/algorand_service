import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
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
import 'by_the_numbers.dart';
import 'feed_placement_card.dart';
import 'story_row.dart';

/// The paper's front page: a section rail, a lead story, a top-stories grid,
/// the latest file, and the standing footer. This is the app's landing route.
class FrontPage extends ConsumerStatefulWidget {
  const FrontPage({super.key});

  @override
  ConsumerState<FrontPage> createState() => _FrontPageState();
}

class _FrontPageState extends ConsumerState<FrontPage> {
  List<Map<String, dynamic>> _items = const [];
  List<Map<String, dynamic>> _hot = const [];
  Map<String, dynamic>? _placement;
  Map<String, dynamic>? _price;
  List<({int epoch, double price})> _priceHistory = const [];
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

  // Idle-priority task instead of a fixed timer: fetch placements as soon as
  // the scheduler has spare time after first paint, not on an arbitrary delay.
  void _loadPlacementsDeferred() {
    SchedulerBinding.instance.scheduleTask(
      () => unawaited(_loadPlacements()),
      Priority.idle,
    );
    SchedulerBinding.instance.scheduleTask(
      () => unawaited(_loadHot()),
      Priority.idle,
    );
    SchedulerBinding.instance.scheduleTask(
      () => unawaited(_loadNumbers()),
      Priority.idle,
    );
  }

  /// "By the numbers" module data. Best-effort: without it the front page
  /// simply renders without the panel.
  Future<void> _loadNumbers() async {
    try {
      final client = ref.read(apiClientProvider);
      final price = await client.getJson('/api/v1/metrics/price');
      if (price['available'] != true) return;
      List<({int epoch, double price})> history = const [];
      try {
        final raw = await client.getJson('/api/v1/metrics/price/history');
        final points = raw['points'];
        if (points is List) {
          history = [
            for (final p in points.whereType<Map<String, dynamic>>())
              if (p['epoch'] is int && p['price_usd'] is num)
                (epoch: p['epoch'] as int, price: (p['price_usd'] as num).toDouble()),
          ];
        }
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _price = price;
        _priceHistory = history;
      });
    } catch (_) {}
  }

  /// Most-read module. Best-effort: on any failure the front page simply
  /// renders without it.
  Future<void> _loadHot() async {
    try {
      final lang = contentLanguageCode(ref, context);
      final hot = await NewsApi(ref.read(apiClientProvider)).fetchHot(limit: 6, lang: lang);
      if (!mounted) return;
      setState(() => _hot = hot);
    } catch (_) {}
  }

  /// Re-fetch after painting SSR-hydrated content so the feed stays fresh.
  Future<void> _refreshInBackground() async {
    await Future<void>.delayed(const Duration(seconds: 5));
    if (!mounted) return;
    await _load(silent: true);
    if (!mounted) return;
    await _loadPlacements();
    if (!mounted) return;
    await _loadHot();
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

  /// The lead must carry a photograph — a text-only lead above an image-rich
  /// grid inverts the visual hierarchy (the grid reads as more important).
  /// Promote the newest story with a real photo from the top of the feed;
  /// only if none of the top stories has one does the plain first item lead.
  ///
  /// image_url is already dimension-vetted server-side
  /// (_validated_hero_checked measures the real pixels), so no URL-shape
  /// re-guess here — a decent app icon shouldn't be skipped just because its
  /// URL "looks like" a logo (was picking a WORSE image with a broken/decoy
  /// backing content just because its URL didn't look like a logo, 2026-07-14).
  static int _leadIndex(List<Map<String, dynamic>> items) {
    final window = items.length < 5 ? items.length : 5;
    for (var i = 0; i < window; i++) {
      final url = items[i]['image_url']?.toString();
      if (url != null && url.isNotEmpty) return i;
    }
    return 0;
  }

  List<Widget> _buildBody(BuildContext context) {
    if (_items.isEmpty) return const [];
    final l10n = context.l10n;
    final leadIdx = _leadIndex(_items);
    final lead = _items[leadIdx];
    final others = [
      for (var i = 0; i < _items.length; i++)
        if (i != leadIdx) _items[i],
    ];
    final secondary = others.take(4).toList();
    final rest = others.skip(4).toList();
    final twoCol = MediaQuery.sizeOf(context).width >= 700;

    return [
      FadeIn(child: ArticleCard(item: lead, hero: true, onTap: () => _open(lead))),
      // The secondary stories are the tail of the lead package, not their own
      // department — an unlabeled continuation, like a broadsheet front.
      // ("Top stories" as a header read as a synonym of "Most read".)
      if (secondary.isNotEmpty) ...[
        const SizedBox(height: AppLayout.itemGap),
        Divider(height: 1, color: context.appColors.border),
        FadeIn(
          child: StoryRowGrid(items: secondary, twoCol: twoCol, onOpen: _open),
        ),
      ],
      if (_price != null) ...[
        const SizedBox(height: AppLayout.sectionGap + 4),
        FadeIn(child: ByTheNumbers(price: _price!, history: _priceHistory)),
      ],
      if (_placement != null) ...[
        const SizedBox(height: AppLayout.sectionGap),
        FadeIn(child: FeedPlacementCard(placement: _placement!)),
      ],
      if (_hot.isNotEmpty) ...[
        const SizedBox(height: AppLayout.sectionGap + 8),
        // Velocity ranking → the honest label is "Hot", not "Most read"
        // (lifetime totals live behind the All-time toggle on /hot).
        _SectionRule(
          text: l10n.navHot,
          onMore: () => context.go('/hot'),
        ),
        const SizedBox(height: 4),
        FadeIn(
          child: StoryRowGrid(
            items: _hot,
            twoCol: twoCol,
            onOpen: _open,
            dense: true,
            ranked: true,
            columnMajor: true,
          ),
        ),
      ],
      // Everything after the lead package, chronological. Deliberately NOT
      // labeled "Latest": the newest five stories are the lead + grid above,
      // so this file is the continuation, not the start.
      if (rest.isNotEmpty) ...[
        const SizedBox(height: AppLayout.sectionGap + 8),
        _SectionRule(text: l10n.frontPageMoreNews),
        const SizedBox(height: 4),
        StoryRowGrid(items: rest, twoCol: twoCol, onOpen: _open, dense: true),
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
/// here" without icon decoration.
class _SectionRule extends StatelessWidget {
  const _SectionRule({required this.text, this.onMore});

  final String text;

  /// When set, a quiet "→" affordance at the rule's right edge opens the
  /// department's own page.
  final VoidCallback? onMore;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(width: 34, height: 3, color: colors.accent),
            const SizedBox(width: 12),
            Text(
              text.toUpperCase(),
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.textTheme.titleMedium?.color,
                letterSpacing: 1.4,
                fontWeight: FontWeight.w800,
              ),
            ),
            const Spacer(),
            if (onMore != null)
              IconButton(
                onPressed: onMore,
                icon: const Icon(Icons.east, size: 16),
                color: colors.muted,
                visualDensity: VisualDensity.compact,
              ),
          ],
        ),
        const SizedBox(height: 8),
        Divider(height: 1, color: colors.border),
      ],
    );
  }
}

/// Front-page "Most read" module: the top of the ranked file, two balanced
/// columns on wide screens, dense rows.
