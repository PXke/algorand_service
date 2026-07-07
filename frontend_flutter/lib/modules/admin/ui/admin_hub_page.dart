import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/admin_provider.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/providers/session_providers.dart';
import '../../newspaper/services/news_api.dart';
import '../../sources/ui/sources_page.dart';
import 'analytics_page.dart';
import 'classifier_feedback_page.dart';
import 'compose_sessions_page.dart';
import 'domains_page.dart';
import 'gatekeeper_page.dart';
import 'inbox_page.dart';
import 'publish_queue_page.dart';
import 'tool_insights_page.dart';
import 'training_page.dart';

Tab _adminHubTab(String label, IconData icon) {
  return Tab(
    height: 72,
    child: Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(icon, size: 18),
        const SizedBox(height: 4),
        Text(label),
      ],
    ),
  );
}

/// Admin hub: sources registry, article editor, writer briefs, classifier.
class AdminHubPage extends ConsumerStatefulWidget {
  const AdminHubPage({super.key});

  @override
  ConsumerState<AdminHubPage> createState() => _AdminHubPageState();
}

class _AdminHubPageState extends ConsumerState<AdminHubPage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 13, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final theme = Theme.of(context);
    final colors = context.appColors;

    if (!ref.watch(isAdminWalletProvider)) {
      return PageContent(
        child: EmptyState(
          title: l10n.adminTitle,
          message: l10n.adminAccessDenied,
          icon: Icons.lock_outline,
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Container(
          decoration: BoxDecoration(
            border: Border(bottom: BorderSide(color: colors.border)),
          ),
          child: Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: AppLayout.maxContentWidth),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: EdgeInsets.fromLTRB(
                        MediaQuery.sizeOf(context).width < 520 ? 16 : 32,
                        16,
                        MediaQuery.sizeOf(context).width < 520 ? 16 : 32,
                        0),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(l10n.adminTitle, style: theme.textTheme.headlineSmall),
                              const SizedBox(height: 8),
                              Container(
                                width: 48,
                                height: 3,
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(2),
                                  gradient: LinearGradient(
                                    colors: [colors.accent, colors.heroGradientEnd],
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        if (MediaQuery.sizeOf(context).width >= 520) _EditorBadge(),
                      ],
                    ),
                  ),
                  const SizedBox(height: 4),
                  TabBar(
                    controller: _tabs,
                    isScrollable: true,
                    tabAlignment: TabAlignment.start,
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    dividerColor: Colors.transparent,
                    labelStyle: theme.textTheme.titleSmall,
                    unselectedLabelStyle: theme.textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w400,
                    ),
                    tabs: [
                      _adminHubTab(l10n.adminTabSeeds, Icons.hub_outlined),
                      _adminHubTab(l10n.adminTabArticles, Icons.article_outlined),
                      _adminHubTab(l10n.adminTabWriterBriefs, Icons.lightbulb_outline),
                      _adminHubTab(l10n.adminTabClassifier, Icons.rate_review_outlined),
                      _adminHubTab('Queue', Icons.pending_actions_outlined),
                      _adminHubTab('Training', Icons.model_training_outlined),
                      _adminHubTab('Gatekeeper', Icons.verified_outlined),
                      _adminHubTab(l10n.adminTabDomains, Icons.travel_explore_outlined),
                      _adminHubTab(l10n.adminTabToolInsights, Icons.build_outlined),
                      _adminHubTab(l10n.adminTabSessions, Icons.forum_outlined),
                      _adminHubTab('Analytics', Icons.insights_outlined),
                      _adminHubTab('Inbox', Icons.mail_outline),
                      _adminHubTab(l10n.adminTabSystem, Icons.settings_outlined),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
        Expanded(
          child: TabBarView(
            controller: _tabs,
            children: const [
              SourcesPage(),
              _AdminArticlesTab(),
              _AdminBriefsTab(),
              ClassifierFeedbackTab(),
              PublishQueueTab(),
              TrainingTab(),
              GatekeeperTab(),
              DomainsTab(),
              ToolInsightsTab(),
              ComposeSessionsTab(),
              AnalyticsTab(),
              InboxTab(),
              _AdminSystemTab(),
            ],
          ),
        ),
      ],
    );
  }
}

class _EditorBadge extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final address = ref.watch(sessionStateProvider).walletAddress ?? '';
    final short = address.length > 10
        ? '${address.substring(0, 5)}…${address.substring(address.length - 5)}'
        : address;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: colors.accentSoft,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.verified_user_outlined, size: 20, color: theme.colorScheme.primary),
          const SizedBox(width: 6),
          Text(
            short,
            style: theme.textTheme.labelSmall?.copyWith(
              fontFamily: 'monospace',
              color: theme.colorScheme.primary,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Articles
// ---------------------------------------------------------------------------

class _AdminArticlesTab extends ConsumerStatefulWidget {
  const _AdminArticlesTab();

  @override
  ConsumerState<_AdminArticlesTab> createState() => _AdminArticlesTabState();
}

class _AdminArticlesTabState extends ConsumerState<_AdminArticlesTab> {
  final _titleController = TextEditingController();
  final _summaryController = TextEditingController();
  final _bodyController = TextEditingController();

  List<Map<String, dynamic>> _feed = const [];
  String? _selectedId;
  bool _loadingFeed = true;
  bool _loadingArticle = false;
  bool _saving = false;
  bool _deleting = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadFeed();
    ref.listenManual(adminPipelineResetSignalProvider, (prev, next) {
      if (prev != next) _loadFeed();
    });
  }

  @override
  void dispose() {
    _titleController.dispose();
    _summaryController.dispose();
    _bodyController.dispose();
    super.dispose();
  }

  Future<void> _loadFeed() async {
    setState(() {
      _loadingFeed = true;
      _error = null;
    });
    try {
      final items = await NewsApi(ref.read(apiClientProvider)).fetchFeed(limit: 30);
      if (!mounted) return;
      setState(() {
        _feed = items;
        _loadingFeed = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadingFeed = false;
      });
    }
  }

  Future<void> _select(String articleId) async {
    setState(() {
      _selectedId = articleId;
      _loadingArticle = true;
      _error = null;
    });
    try {
      final article = await NewsApi(ref.read(apiClientProvider)).fetchArticle(articleId);
      if (!mounted) return;
      setState(() {
        _titleController.text = article['title']?.toString() ?? '';
        _summaryController.text = article['summary']?.toString() ?? '';
        _bodyController.text = article['body']?.toString() ?? '';
        _loadingArticle = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loadingArticle = false;
      });
    }
  }

  Future<void> _save() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    final id = _selectedId;
    if (wallet == null || id == null) return;
    setState(() => _saving = true);
    try {
      await ref.read(adminApiProvider).patchArticle(
        id,
        walletAddress: wallet,
        title: _titleController.text,
        summary: _summaryController.text,
        body: _bodyController.text,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Article saved')),
      );
      await _loadFeed();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    final id = _selectedId;
    if (wallet == null || id == null) return;

    final title = _titleController.text.trim();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete article?'),
        content: Text(
          title.isEmpty
              ? 'This article will be removed from the feed permanently.'
              : '"$title" will be removed from the feed permanently.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(ctx).colorScheme.error,
            ),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() {
      _deleting = true;
      _error = null;
    });
    try {
      await ref.read(adminApiProvider).deleteArticle(
        walletAddress: wallet,
        articleId: id,
      );
      if (!mounted) return;
      setState(() {
        _selectedId = null;
        _titleController.clear();
        _summaryController.clear();
        _bodyController.clear();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Article deleted')),
      );
      await _loadFeed();
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return LayoutBuilder(
      builder: (context, constraints) {
        final twoPane = constraints.maxWidth >= 900;
        final list = _ArticlePickerList(
          feed: _feed,
          selectedId: _selectedId,
          loading: _loadingFeed,
          onSelect: _select,
          onRefresh: _loadFeed,
        );
        final editor = _buildEditor(theme, colors);

        if (!twoPane) {
          return PageScroll(
            refresh: _loadFeed,
            children: [
              if (_error != null) ErrorBanner(message: _error!),
              SizedBox(height: 300, child: _SectionCard(child: list)),
              const SizedBox(height: AppLayout.itemGap),
              editor,
            ],
          );
        }

        return Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: AppLayout.maxContentWidth),
            child: Padding(
              padding: AppLayout.pagePadding,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (_error != null) ErrorBanner(message: _error!),
                  Expanded(
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        SizedBox(width: 300, child: _SectionCard(child: list)),
                        const SizedBox(width: AppLayout.itemGap),
                        Expanded(child: SingleChildScrollView(child: editor)),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _buildEditor(ThemeData theme, dynamic colors) {
    if (_selectedId == null) {
      return _SectionCard(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(
            children: [
              Icon(Icons.article_outlined, size: 40, color: colors.muted),
              const SizedBox(height: 12),
              Text(
                'Pick an article on the left to edit it.',
                style: theme.textTheme.bodyMedium?.copyWith(color: colors.muted),
              ),
            ],
          ),
        ),
      );
    }

    return _SectionCard(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    _selectedId!,
                    style: theme.textTheme.labelSmall?.copyWith(
                      fontFamily: 'monospace',
                      color: colors.muted,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (_loadingArticle)
                  const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _titleController,
              decoration: const InputDecoration(labelText: 'Title'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _summaryController,
              decoration: const InputDecoration(labelText: 'Summary (deck)'),
              maxLines: 3,
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _bodyController,
              decoration: const InputDecoration(
                labelText: 'Body (markdown)',
                alignLabelWithHint: true,
              ),
              style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace', height: 1.6),
              maxLines: 18,
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                TextButton.icon(
                  onPressed: (_loadingArticle || _saving || _deleting) ? null : _delete,
                  icon: _deleting
                      ? SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: theme.colorScheme.error,
                          ),
                        )
                      : Icon(Icons.delete_outline, size: 18, color: theme.colorScheme.error),
                  label: Text('Delete', style: TextStyle(color: theme.colorScheme.error)),
                ),
                const Spacer(),
                OutlinedButton.icon(
                  onPressed: _loadingArticle ? null : () => _select(_selectedId!),
                  icon: const Icon(Icons.refresh, size: 18),
                  label: const Text('Reload'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: (_saving || _deleting) ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.save_outlined, size: 18),
                  label: const Text('Save'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ArticlePickerList extends StatelessWidget {
  const _ArticlePickerList({
    required this.feed,
    required this.selectedId,
    required this.loading,
    required this.onSelect,
    required this.onRefresh,
  });

  final List<Map<String, dynamic>> feed;
  final String? selectedId;
  final bool loading;
  final void Function(String) onSelect;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 8, 4),
          child: Row(
            children: [
              Expanded(child: Text('Recent articles', style: theme.textTheme.titleSmall)),
              IconButton(
                tooltip: 'Refresh',
                iconSize: 18,
                visualDensity: VisualDensity.compact,
                onPressed: loading ? null : onRefresh,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
        ),
        LoadingStrip(visible: loading),
        Expanded(
          child: feed.isEmpty && !loading
              ? Center(
                  child: Text(
                    'No articles yet.',
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                  ),
                )
              : ListView.separated(
                  itemCount: feed.length,
                  separatorBuilder: (_, _) => Divider(height: 1, color: colors.border),
                  itemBuilder: (context, index) {
                    final item = feed[index];
                    final id = item['article_id']?.toString() ?? item['id']?.toString() ?? '';
                    final selected = id == selectedId;
                    return ListTile(
                      dense: true,
                      selected: selected,
                      selectedTileColor: colors.accentSoft,
                      title: Text(
                        item['title']?.toString() ?? id,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: theme.textTheme.bodySmall?.copyWith(
                          fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                        ),
                      ),
                      subtitle: Text(
                        item['published_at']?.toString().split('T').first ?? '',
                        style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                      ),
                      onTap: id.isEmpty ? null : () => onSelect(id),
                    );
                  },
                ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Writer briefs
// ---------------------------------------------------------------------------

class _AdminBriefsTab extends ConsumerStatefulWidget {
  const _AdminBriefsTab();

  @override
  ConsumerState<_AdminBriefsTab> createState() => _AdminBriefsTabState();
}

class _AdminBriefsTabState extends ConsumerState<_AdminBriefsTab> {
  final _titleController = TextEditingController();
  final _keywordsController = TextEditingController();
  final _bodyController = TextEditingController();
  final _refreshDaysController = TextEditingController(text: '0');
  List<Map<String, dynamic>> _briefs = [];
  bool _submitting = false;
  String? _error;
  String? _assigningBriefId;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  @override
  void dispose() {
    _titleController.dispose();
    _keywordsController.dispose();
    _bodyController.dispose();
    _refreshDaysController.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null) return;
    try {
      final items = await ref.read(adminApiProvider).listBriefs(walletAddress: wallet);
      if (!mounted) return;
      setState(() {
        _briefs = items;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    }
  }

  Future<void> _create() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null || _titleController.text.trim().isEmpty) return;
    setState(() => _submitting = true);
    try {
      final refreshDays = int.tryParse(_refreshDaysController.text.trim()) ?? 0;
      await ref.read(adminApiProvider).createBrief(
        walletAddress: wallet,
        title: _titleController.text.trim(),
        bodyMarkdown: _bodyController.text,
        keywords: _keywordsController.text.trim(),
        refreshEveryDays: refreshDays,
      );
      _titleController.clear();
      _bodyController.clear();
      _keywordsController.clear();
      _refreshDaysController.text = '0';
      await _refresh();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Brief assigned to the writer agent')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _assignNow(String briefId) async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null) return;
    setState(() => _assigningBriefId = briefId);
    try {
      await ref.read(adminApiProvider).assignBriefNow(walletAddress: wallet, briefId: briefId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Queued for the writer agent')),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _assigningBriefId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return PageScroll(
      refresh: _refresh,
      children: [
        if (_error != null) ErrorBanner(message: _error!),
        _SectionCard(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Assign the writer a topic', style: theme.textTheme.titleSmall),
                const SizedBox(height: 4),
                Text(
                  'Writes an original article on this topic now. Set a refresh cadence to '
                  'keep it updated in place instead of writing a new one each time.',
                  style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _titleController,
                  decoration: const InputDecoration(labelText: 'Topic / working title'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _keywordsController,
                  decoration: const InputDecoration(
                    labelText: 'Focus keywords',
                    hintText: 'comma-separated',
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _bodyController,
                  decoration: const InputDecoration(
                    labelText: 'Editorial pointers (markdown)',
                    alignLabelWithHint: true,
                  ),
                  maxLines: 6,
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _refreshDaysController,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Refresh every N days',
                    hintText: '0 = one-off, no recurring refresh',
                  ),
                ),
                const SizedBox(height: 16),
                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton.icon(
                    onPressed: _submitting ? null : _create,
                    icon: _submitting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.send_outlined, size: 18),
                    label: const Text('Assign to writer'),
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: AppLayout.sectionGap),
        Text('Assigned briefs', style: theme.textTheme.titleSmall),
        const SizedBox(height: AppLayout.itemGap),
        if (_briefs.isEmpty)
          Text(
            'Nothing assigned yet.',
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
        ..._briefs.map(
          (b) {
            final briefId = b['brief_id']?.toString() ?? '';
            final refreshDays = (b['refresh_every_days'] as num?)?.toInt() ?? 0;
            final linked = b['linked_article_id']?.toString() ?? '';
            final lastRunEpoch = (b['last_run_at_epoch'] as num?)?.toInt() ?? 0;
            final cadence = refreshDays > 0 ? 'Refreshes every $refreshDays d' : 'One-off';
            final lastRun = lastRunEpoch > 0
                ? DateTime.fromMillisecondsSinceEpoch(lastRunEpoch * 1000).toIso8601String()
                : 'not run yet';
            final subtitleParts = [
              if (b['keywords']?.toString().isNotEmpty ?? false) b['keywords'].toString(),
              cadence,
              linked.isEmpty ? 'no article yet' : 'last run: $lastRun',
            ];
            return Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _SectionCard(
                child: ListTile(
                  title: Text(b['title']?.toString() ?? '', style: theme.textTheme.bodyMedium),
                  subtitle: Text(
                    subtitleParts.join(' · '),
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                  ),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      _StatusChip(text: b['status']?.toString() ?? ''),
                      const SizedBox(width: 8),
                      IconButton(
                        tooltip: linked.isEmpty ? 'Write now' : 'Refresh now',
                        icon: _assigningBriefId == briefId
                            ? const SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.play_arrow_outlined, size: 20),
                        onPressed: _assigningBriefId == briefId || briefId.isEmpty
                            ? null
                            : () => _assignNow(briefId),
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

class _SectionCard extends StatelessWidget {
  const _SectionCard({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Container(
      decoration: BoxDecoration(
        color: Theme.of(context).cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: child,
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: colors.calloutBackground,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: colors.border),
      ),
      child: Text(
        text,
        style: theme.textTheme.labelSmall?.copyWith(
          color: colors.muted,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// System status
// ---------------------------------------------------------------------------

class _AdminSystemTab extends ConsumerStatefulWidget {
  const _AdminSystemTab();

  @override
  ConsumerState<_AdminSystemTab> createState() => _AdminSystemTabState();
}

class _AdminSystemTabState extends ConsumerState<_AdminSystemTab> {
  List<Map<String, dynamic>> _checks = const [];
  List<Map<String, dynamic>> _workers = const [];
  List<Map<String, dynamic>> _actions = const [];
  String? _workersError;
  String? _status;
  bool _loading = true;
  String? _error;
  DateTime? _fetchedAt;
  final Set<String> _running = {};
  bool _resetting = false;

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
    final wallet = ref.read(sessionStateProvider).walletAddress;
    final api = ref.read(adminApiProvider);
    try {
      final health = ref.read(apiClientProvider).getJson('/health/ready');
      final workers = wallet == null
          ? Future.value(const <Map<String, dynamic>>[])
          : api.celeryWorkers(walletAddress: wallet);
      final actions = wallet == null
          ? Future.value(const <Map<String, dynamic>>[])
          : (_actions.isEmpty
              ? api.listScrapers(walletAddress: wallet)
              : Future.value(_actions));

      final body = await health;
      List<Map<String, dynamic>> workerList = const [];
      String? workersError;
      try {
        workerList = await workers;
      } catch (e) {
        workersError = e.toString();
      }
      final actionList = await actions;

      if (!mounted) return;
      setState(() {
        _status = body['status']?.toString();
        _checks = (body['checks'] as List? ?? const [])
            .whereType<Map<String, dynamic>>()
            .toList();
        _workers = workerList;
        _workersError = workersError;
        _actions = actionList;
        _fetchedAt = DateTime.now();
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

  Future<void> _runAll() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null || _actions.isEmpty) return;
    setState(() => _running.addAll(_actions.map((a) => a['action'].toString())));
    final api = ref.read(adminApiProvider);
    var queued = 0;
    String? failure;
    for (final action in _actions) {
      try {
        await api.runScraper(
          walletAddress: wallet,
          action: action['action'].toString(),
        );
        queued++;
      } catch (e) {
        failure = e.toString();
      }
    }
    if (!mounted) return;
    setState(_running.clear);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          failure == null
              ? 'Queued all $queued tasks'
              : 'Queued $queued tasks; last failure: $failure',
        ),
      ),
    );
  }

  bool _clearingDomains = false;

  Future<void> _clearDomains() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear explored domains?'),
        content: const Text(
          'Forgets the whole crawl frontier: explored, pending and dead-end '
          'domains. The crawler re-discovers (and re-holds) them from scratch. '
          'The platform blocklist is unaffected.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(backgroundColor: Theme.of(ctx).colorScheme.error),
            child: const Text('Clear domains'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _clearingDomains = true);
    try {
      await ref.read(adminApiProvider).clearDomains(walletAddress: wallet);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Domain frontier cleared')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Clear failed: $e')));
    } finally {
      if (mounted) setState(() => _clearingDomains = false);
    }
  }

  Future<void> _resetPipeline() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reset pipeline?'),
        content: const Text(
          'This wipes all articles, publish queues, crawl queues, and search '
          'indexes so the pipeline can start fresh.\n\n'
          'Sources, classifier feedback, and pending reviews are kept.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(ctx).colorScheme.error,
            ),
            child: const Text('Reset all'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _resetting = true);
    try {
      final result = await ref.read(adminApiProvider)
          .resetPipeline(walletAddress: wallet);
      if (!mounted) return;
      ref.read(adminPipelineResetSignalProvider.notifier).bump();
      final tables = (result['tables'] as List?)?.length ?? 0;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Pipeline reset — cleared $tables tables')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Reset failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _resetting = false);
    }
  }

  Future<void> _run(Map<String, dynamic> action) async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    final key = action['action']?.toString() ?? '';
    if (wallet == null || key.isEmpty) return;
    setState(() => _running.add(key));
    try {
      final result = await ref.read(adminApiProvider)
          .runScraper(walletAddress: wallet, action: key);
      if (!mounted) return;
      final taskId = result['task_id']?.toString() ?? '';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Queued "${action['label']}"'
            '${taskId.isNotEmpty ? ' (task ${taskId.substring(0, 8)}…)' : ''}',
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed: $e')),
      );
    } finally {
      if (mounted) setState(() => _running.remove(key));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return PageScroll(
      refresh: _load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Live backend readiness — the same checks the deploy pipeline gates on.',
                style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
              ),
            ),
            if (_fetchedAt != null)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Text(
                  'as of ${_fetchedAt!.toLocal().toString().substring(11, 19)}',
                  style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                ),
              ),
            IconButton(
              tooltip: 'Refresh',
              iconSize: 18,
              visualDensity: VisualDensity.compact,
              onPressed: _loading ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: AppLayout.itemGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (_status != null) ...[
          _overallBanner(theme, colors),
          const SizedBox(height: AppLayout.itemGap),
        ],
        _workersCard(theme, colors),
        const SizedBox(height: AppLayout.itemGap),
        ..._checks.map((check) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _checkCard(theme, colors, check),
            )),
        if (_actions.isNotEmpty) ...[
          const SizedBox(height: AppLayout.sectionGap - AppLayout.itemGap),
          Row(
            children: [
              Expanded(child: Text('Run tasks now', style: theme.textTheme.titleSmall)),
              FilledButton.tonalIcon(
                onPressed: _running.isNotEmpty ? null : _runAll,
                icon: const Icon(Icons.playlist_play_rounded, size: 18),
                label: const Text('Run all'),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            'Queue a worker task immediately instead of waiting for its schedule.',
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
          const SizedBox(height: AppLayout.itemGap),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: _actions.map((a) {
              final key = a['action']?.toString() ?? '';
              final busy = _running.contains(key);
              return Tooltip(
                message: a['description']?.toString() ?? '',
                waitDuration: const Duration(milliseconds: 400),
                child: OutlinedButton.icon(
                  onPressed: busy ? null : () => _run(a),
                  icon: busy
                      ? const SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.play_arrow_rounded, size: 18),
                  label: Text(a['label']?.toString() ?? key),
                ),
              );
            }).toList(),
          ),
        ],
        const SizedBox(height: AppLayout.sectionGap),
        Row(
          children: [
            Expanded(child: Text('Beta tools', style: theme.textTheme.titleSmall)),
            FilledButton.tonalIcon(
              onPressed: _clearingDomains ? null : _clearDomains,
              icon: _clearingDomains
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Icon(Icons.travel_explore_outlined, size: 18, color: theme.colorScheme.error),
              label: Text('Clear domains', style: TextStyle(color: theme.colorScheme.error)),
            ),
            const SizedBox(width: 8),
            FilledButton.tonalIcon(
              onPressed: _resetting ? null : _resetPipeline,
              icon: _resetting
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Icon(Icons.delete_sweep_outlined, size: 18, color: theme.colorScheme.error),
              label: Text('Reset all', style: TextStyle(color: theme.colorScheme.error)),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          'Truncate article, publish, crawl, and search state. Irreversible.',
          style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
        ),
      ],
    );
  }

  Widget _workersCard(ThemeData theme, AppThemeColors colors) {
    final okColor = const Color(0xFF2E7D32);
    final warn = theme.colorScheme.error;

    Widget content;
    if (_workersError != null) {
      content = Text(
        'Worker ping failed: $_workersError',
        style: theme.textTheme.bodySmall?.copyWith(color: warn),
      );
    } else if (_workers.isEmpty) {
      content = Text(
        _loading
            ? 'Pinging workers…'
            : 'No Celery workers answered the ping — the worker service may be down.',
        style: theme.textTheme.bodySmall?.copyWith(
          color: _loading ? colors.muted : warn,
        ),
      );
    } else {
      content = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: _workers.map((w) {
          final online = w['online'] == true;
          final active = w['active_tasks'] ?? 0;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 3),
            child: Row(
              children: [
                Container(
                  width: 9,
                  height: 9,
                  decoration: BoxDecoration(
                    color: online ? okColor : warn,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    w['name']?.toString() ?? '',
                    style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(
                  online ? '$active active' : 'offline',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: online ? colors.muted : warn,
                  ),
                ),
              ],
            ),
          );
        }).toList(),
      );
    }

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Celery workers', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          content,
        ],
      ),
    );
  }

  Widget _overallBanner(ThemeData theme, AppThemeColors colors) {
    final ok = _status == 'ok';
    final color = ok ? const Color(0xFF2E7D32) : theme.colorScheme.error;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          Icon(ok ? Icons.check_circle_outline : Icons.error_outline, size: 18, color: color),
          const SizedBox(width: 10),
          Text(
            ok ? 'All systems operational' : 'Status: $_status',
            style: theme.textTheme.titleSmall?.copyWith(color: color),
          ),
        ],
      ),
    );
  }

  Widget _checkCard(ThemeData theme, AppThemeColors colors, Map<String, dynamic> check) {
    final name = check['name']?.toString() ?? '';
    final ok = check['ok'] == true;
    final detail = check['detail']?.toString() ?? '';
    final dotColor = ok ? const Color(0xFF2E7D32) : theme.colorScheme.error;

    Widget detailWidget;
    if (name == 'celery_queues' && detail.startsWith('total=')) {
      // "total=3 scrape=1, pipeline=2, default=0, ..." → chips per queue
      final parts = detail.replaceAll(',', '').split(' ').where((p) => p.contains('='));
      detailWidget = Wrap(
        spacing: 6,
        runSpacing: 6,
        children: parts.map((p) {
          final kv = p.split('=');
          final depth = int.tryParse(kv.length > 1 ? kv[1] : '') ?? 0;
          final highlight = depth > 0;
          return Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: highlight ? colors.accentSoft : colors.calloutBackground,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: colors.border),
            ),
            child: Text(
              '${kv[0]} ${kv.length > 1 ? kv[1] : ''}',
              style: theme.textTheme.labelSmall?.copyWith(
                fontFamily: 'monospace',
                color: highlight ? theme.colorScheme.primary : colors.muted,
                fontWeight: highlight ? FontWeight.w700 : FontWeight.w400,
              ),
            ),
          );
        }).toList(),
      );
    } else {
      detailWidget = Text(
        detail.isEmpty ? (ok ? 'healthy' : 'failing') : detail,
        style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
      );
    }

    final label = switch (name) {
      'celery_queues' => 'Celery queues',
      'conduit_index' => 'Conduit chain index',
      _ => name[0].toUpperCase() + name.substring(1),
    };

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 3),
            child: Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(color: dotColor, shape: BoxShape.circle),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label, style: theme.textTheme.titleSmall),
                const SizedBox(height: 6),
                detailWidget,
              ],
            ),
          ),
        ],
      ),
    );
  }
}
