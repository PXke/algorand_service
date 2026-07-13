import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/api/api_errors.dart';
import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/l10n/locale_provider.dart';
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
import 'story_row.dart';

enum _ShareTarget { whatsapp, bluesky, telegram, x, facebook }

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
      final lang = contentLanguageCode(ref, context);
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

  /// Absolute URL for this article — a bare path is useless outside the app.
  /// Uri.base is the page's own origin on web, so this works in dev and prod
  /// without config.
  String _articleUrl(Map<String, dynamic> article) {
    final id = article['article_id']?.toString() ?? '';
    return Uri.base.origin.isEmpty
        ? '/news/articles/$id'
        : '${Uri.base.origin}/news/articles/$id';
  }

  Future<void> _copyLink(BuildContext context, Map<String, dynamic> article) async {
    await Clipboard.setData(ClipboardData(text: _articleUrl(article)));
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(context.l10n.articleLinkCopied)),
    );
  }

  /// Every target below is a plain share-intent URL — no API key, no app
  /// registration, no auth. Opens the platform's own pre-filled share/compose
  /// dialog in a new tab; the visitor still has to hit send.
  Future<void> _shareVia(_ShareTarget target, Map<String, dynamic> article) async {
    final url = _articleUrl(article);
    final title = article['title']?.toString() ?? '';
    final uri = switch (target) {
      _ShareTarget.whatsapp => Uri.https('wa.me', '', {'text': '$title $url'}),
      _ShareTarget.bluesky =>
        Uri.https('bsky.app', '/intent/compose', {'text': '$title $url'}),
      _ShareTarget.telegram =>
        Uri.https('t.me', '/share/url', {'url': url, 'text': title}),
      _ShareTarget.x =>
        Uri.https('twitter.com', '/intent/tweet', {'text': title, 'url': url}),
      _ShareTarget.facebook =>
        Uri.https('www.facebook.com', '/sharer/sharer.php', {'u': url}),
    };
    await launchUrl(uri, mode: LaunchMode.externalApplication);
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
    ref.listen(localeProvider, (previous, next) {
      if (previous != next) _load();
    });
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
                    // Kicker: accent slug + the story's primary writer tag in
                    // small caps — the same department mark the front page uses.
                    if (tags.isNotEmpty) ...[
                      Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: Container(width: 34, height: 3, color: colors.accent),
                      ),
                      const SizedBox(height: 14),
                      Text(
                        displayTagLabel(primaryTag(tags) ?? tags.first).toUpperCase(),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.labelSmall?.copyWith(
                          color: colors.accent,
                          letterSpacing: 1.4,
                          fontWeight: FontWeight.w700,
                        ),
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
                          PopupMenuButton<_ShareTarget?>(
                            tooltip: l10n.articleShare,
                            icon: const Icon(Icons.ios_share, size: 18),
                            onSelected: (target) => target == null
                                ? _copyLink(context, article)
                                : _shareVia(target, article),
                            itemBuilder: (context) => [
                              const PopupMenuItem(
                                value: _ShareTarget.whatsapp,
                                child: Text('WhatsApp'),
                              ),
                              const PopupMenuItem(
                                value: _ShareTarget.bluesky,
                                child: Text('Bluesky'),
                              ),
                              const PopupMenuItem(
                                value: _ShareTarget.telegram,
                                child: Text('Telegram'),
                              ),
                              const PopupMenuItem(
                                value: _ShareTarget.x,
                                child: Text('X'),
                              ),
                              const PopupMenuItem(
                                value: _ShareTarget.facebook,
                                child: Text('Facebook'),
                              ),
                              const PopupMenuDivider(),
                              PopupMenuItem(
                                value: null,
                                child: Text(l10n.articleShareCopyLink),
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                    if (tags.length > 1) ...[
                      const SizedBox(height: 14),
                      ArticleTagRow(
                        tags: (List.of(tags)..remove(primaryTag(tags) ?? tags.first))
                            .take(4)
                            .toList(),
                        compact: true,
                        linkToTopic: true,
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
                      for (var i = 0; i < _related.length; i++)
                        StoryRow(
                          item: _related[i],
                          first: i == 0,
                          onTap: () {
                            final id =
                                _related[i]['article_id']?.toString() ?? '';
                            if (id.isNotEmpty) {
                              context.go('/news/articles/$id');
                            }
                          },
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
