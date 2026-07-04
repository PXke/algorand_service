import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/ui/deferred_article_markdown.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/fade_in.dart';
import '../../../core/ui/format.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/article_tag_chip.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/ui/publication_details_panel.dart';
import '../sections.dart';
import '../services/news_api.dart';
import 'article_card.dart';

class ArticleDetailPage extends ConsumerStatefulWidget {
  const ArticleDetailPage({super.key, required this.articleId});

  final String articleId;

  @override
  ConsumerState<ArticleDetailPage> createState() => _ArticleDetailPageState();
}

class _ArticleDetailPageState extends ConsumerState<ArticleDetailPage> {
  Map<String, dynamic>? _article;
  List<Map<String, dynamic>> _related = const [];
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant ArticleDetailPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Navigating between articles (e.g. a related-story tap) reuses this same
    // page/State — initState won't re-run — so refetch when the id changes,
    // otherwise the URL updates but the previous story stays on screen.
    if (oldWidget.articleId != widget.articleId) {
      _load();
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
      _related = const [];
    });
    try {
      final lang = Localizations.localeOf(context).languageCode;
      final article = await NewsApi(ref.read(apiClientProvider)).fetchArticle(widget.articleId, lang: lang);
      if (!mounted) return;
      setState(() {
        _article = article;
        _loading = false;
      });
      _loadRelated(article, lang);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = apiErrorMessage(e);
        _loading = false;
      });
    }
  }

  /// Pulls the recent feed and keeps stories that share a tag with this one.
  Future<void> _loadRelated(Map<String, dynamic> article, String lang) async {
    try {
      final page = await NewsApi(ref.read(apiClientProvider)).fetchFeedPage(limit: 50, lang: lang);
      final tags = _articleTags(article).map((t) => t.toLowerCase()).toSet();
      if (tags.isEmpty) return;
      final id = article['article_id']?.toString();
      final related = page.items.where((item) {
        if (item['article_id']?.toString() == id) return false;
        return tagsOf(item).map((t) => t.toLowerCase()).any(tags.contains);
      }).take(4).toList();
      if (!mounted) return;
      setState(() => _related = related);
    } catch (_) {}
  }

  int _readingMinutes(String body) {
    final words =
        body.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty).length;
    if (words == 0) return 1;
    return (words / 220).ceil().clamp(1, 999);
  }

  String _byline(BuildContext context, Map<String, dynamic> article) {
    final l10n = context.l10n;
    final desk = switch (_triggerKind(article)) {
      'chain' => l10n.bylineChainDesk,
      'scheduled' => l10n.bylineMarketsDesk,
      _ => l10n.bylineNewsroom,
    };
    return l10n.articleByline(desk);
  }

  Future<void> _share(BuildContext context, Map<String, dynamic> article) async {
    final id = article['article_id']?.toString() ?? '';
    // Absolute URL — a bare path is useless outside the app. Uri.base is the
    // page's own origin on web, so this works in dev and prod without config.
    final url = Uri.base.origin.isEmpty
        ? '/news/articles/$id'
        : '${Uri.base.origin}/news/articles/$id';
    await Clipboard.setData(ClipboardData(text: url));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(context.l10n.articleLinkCopied)),
    );
  }

  List<String> _articleTags(Map<String, dynamic> article) {
    return (article['tags'] as List<dynamic>?)
            ?.map((t) => t.toString())
            .where((t) => t.isNotEmpty)
            .toList() ??
        const [];
  }

  String _triggerKind(Map<String, dynamic> article) {
    final fromApi = article['trigger_kind']?.toString();
    if (fromApi != null && fromApi.isNotEmpty) {
      return fromApi;
    }
    final tx = article['trigger_txid']?.toString() ?? '';
    final tags = _articleTags(article).map((t) => t.toLowerCase()).toSet();
    final sid = article['service_id']?.toString().toLowerCase() ?? '';
    if (tags.contains('weekly') || sid.startsWith('weekly') || tx.startsWith('weekly')) {
      return 'scheduled';
    }
    if (tx.length == 52 && tx == tx.toUpperCase()) {
      return 'chain';
    }
    return 'editorial';
  }

  Future<void> _openSourceUrl(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final article = _article;
    final theme = Theme.of(context);
    final colors = context.appColors;
    final isMobile = MediaQuery.sizeOf(context).width < 520;

    final tags = article == null ? const <String>[] : _articleTags(article);
    final summary = stripMarkdown(article?['summary']?.toString() ?? '');

    return PageScroll(
      children: [
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (article != null)
          FadeIn(
            child: PageContent(
            child: Align(
              alignment: Alignment.topCenter,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: AppLayout.maxReadingWidth),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // Kicker: section glyph + label in small caps, accented.
                    if (tags.isNotEmpty) ...[
                      Row(
                        children: [
                          Icon(
                            sectionForTags(tags)?.icon ?? Icons.label_outline,
                            size: 15,
                            color: colors.accent,
                          ),
                          const SizedBox(width: 7),
                          Flexible(
                            child: Text(
                              (sectionForTags(tags)?.label(context) ?? tags.first)
                                  .toUpperCase(),
                              style: theme.textTheme.labelSmall?.copyWith(
                                color: colors.accent,
                                letterSpacing: 1.4,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 14),
                    ],
                    Text(
                      article['title']?.toString() ?? '',
                      style: theme.textTheme.headlineLarge?.copyWith(
                        height: 1.12,
                        fontSize: isMobile ? 28 : 38,
                      ),
                    ),
                    if (summary.isNotEmpty) ...[
                      const SizedBox(height: 18),
                      // Deck: standfirst paragraph under the headline.
                      Text(
                        summary,
                        style: theme.textTheme.bodyLarge?.copyWith(
                          fontSize: isMobile ? 18 : 20,
                          height: 1.55,
                          color: colors.muted,
                        ),
                      ),
                    ],
                    const SizedBox(height: 22),
                    // Byline strip between hairline rules, newspaper style:
                    // desk byline, then date + reading time, then a share action.
                    Container(
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      decoration: BoxDecoration(
                        border: Border(
                          top: BorderSide(color: colors.border),
                          bottom: BorderSide(color: colors.border),
                        ),
                      ),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  _byline(context, article),
                                  style: theme.textTheme.labelLarge?.copyWith(
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                const SizedBox(height: 2),
                                Text(
                                  [
                                    formatArticleMetaLine(
                                      context,
                                      publishedEpoch:
                                          article['published_at_epoch'] as int?,
                                    ),
                                    l10n.articleReadingTime(
                                      _readingMinutes(
                                        article['body']?.toString() ?? '',
                                      ),
                                    ),
                                  ].where((s) => s.isNotEmpty).join('  ·  '),
                                  style: theme.textTheme.labelMedium?.copyWith(
                                    color: colors.muted,
                                    letterSpacing: 0.2,
                                  ),
                                ),
                              ],
                            ),
                          ),
                          IconButton(
                            tooltip: l10n.articleShare,
                            onPressed: () => _share(context, article),
                            icon: const Icon(Icons.ios_share, size: 18),
                          ),
                        ],
                      ),
                    ),
                    if (tags.length > 1) ...[
                      const SizedBox(height: 14),
                      ArticleTagRow(
                        tags: tags.sublist(1).take(4).toList(),
                        compact: true,
                      ),
                    ],
                    const SizedBox(height: 30),
                    DeferredArticleMarkdown(data: article['body']?.toString() ?? ''),
                    const SizedBox(height: AppLayout.sectionGap),
                    const Divider(),
                    const SizedBox(height: AppLayout.sectionGap),
                    PublicationDetailsPanel(
                      publisher: article['service_id']?.toString(),
                      publishedLabel: formatRelativeEpoch(
                        context,
                        article['published_at_epoch'] as int?,
                      ),
                      sourceUrl: article['source_url']?.toString(),
                      dataSourceLabel: _triggerKind(article) == 'scheduled'
                          ? l10n.articleMetaCoinGecko
                          : null,
                      onOpenUrl: _openSourceUrl,
                    ),
                    const SizedBox(height: AppLayout.itemGap),
                    if (article['service_id'] != null)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: OutlinedButton.icon(
                          onPressed: () =>
                              context.go('/news?service_id=${article['service_id']}'),
                          icon: const Icon(Icons.filter_list, size: 18),
                          label: Text(l10n.articleMoreFrom(article['service_id'].toString())),
                        ),
                      ),
                    if (_related.isNotEmpty) ...[
                      const SizedBox(height: AppLayout.sectionGap),
                      Row(
                        children: [
                          Text(
                            l10n.articleRelatedTitle.toUpperCase(),
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: colors.subtle,
                              letterSpacing: 0.9,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(child: Divider(height: 1, color: colors.border)),
                        ],
                      ),
                      const SizedBox(height: AppLayout.itemGap),
                      for (final item in _related)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: ArticleCard(
                            item: item,
                            onTap: () {
                              final id = item['article_id']?.toString() ?? '';
                              if (id.isNotEmpty) context.go('/news/articles/$id');
                            },
                          ),
                        ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
