import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/providers/session_providers.dart';

/// Admin tab: writer introspection — tool gaps (suggest_tool) and pipeline
/// feedback (report_compose_issue). Grouped for prioritizing what to fix next.
class ToolInsightsTab extends ConsumerStatefulWidget {
  const ToolInsightsTab({super.key});

  @override
  ConsumerState<ToolInsightsTab> createState() => _ToolInsightsTabState();
}

class _ToolInsightsTabState extends ConsumerState<ToolInsightsTab>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs;

  List<Map<String, dynamic>> _suggestions = const [];
  List<Map<String, dynamic>> _feedback = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tabs = TabController(length: 2, vsync: this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
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
      final api = ref.read(adminApiProvider);
      final results = await Future.wait([
        api.listToolSuggestions(walletAddress: wallet),
        api.listComposeFeedback(walletAddress: wallet),
      ]);
      if (!mounted) return;
      setState(() {
        _suggestions = results[0];
        _feedback = results[1];
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

  List<_CapabilityGroup> _groupedSuggestions() {
    final byCap = <String, _CapabilityGroup>{};
    for (final item in _suggestions) {
      final cap = (item['capability']?.toString() ?? '').trim();
      if (cap.isEmpty) continue;
      final key = cap.toLowerCase();
      final group = byCap.putIfAbsent(key, () => _CapabilityGroup(label: cap));
      group.entries.add(item);
    }
    final groups = byCap.values.toList()
      ..sort((a, b) => b.entries.length.compareTo(a.entries.length));
    return groups;
  }

  List<_FeedbackGroup> _groupedFeedback() {
    final byCat = <String, _FeedbackGroup>{};
    for (final item in _feedback) {
      final cat = (item['category']?.toString() ?? 'other').trim().toLowerCase();
      final group = byCat.putIfAbsent(cat, () => _FeedbackGroup(label: cat));
      group.entries.add(item);
    }
    final groups = byCat.values.toList()
      ..sort((a, b) => b.entries.length.compareTo(a.entries.length));
    return groups;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return SelectionArea(
      child: PageScroll(
        refresh: _load,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Writer introspection while composing — tool gaps and pipeline '
                  'friction the model reports back. Use this to prioritize prompt, '
                  'data, and tool fixes. Hard tool errors still go to Bugsnag.',
                  style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
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
          TabBar(
            controller: _tabs,
            tabs: const [
              Tab(text: 'Tool gaps'),
              Tab(text: 'Pipeline feedback'),
            ],
          ),
          const SizedBox(height: AppLayout.itemGap),
          LoadingStrip(visible: _loading),
          if (_error != null) ErrorBanner(message: _error!),
          if (!_loading && _error == null)
            SizedBox(
              height: 600,
              child: TabBarView(
                controller: _tabs,
                children: [
                  _suggestionsPane(theme, colors),
                  _feedbackPane(theme, colors),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _suggestionsPane(ThemeData theme, AppThemeColors colors) {
    final groups = _groupedSuggestions();
    if (groups.isEmpty) {
      return const EmptyState(
        title: 'No tool suggestions yet',
        message: 'When the writer wishes it had a tool it lacks, it records the gap '
            'here. Nothing yet — that is a good sign the toolset is covering stories.',
        icon: Icons.build_outlined,
      );
    }
    return ListView(
      children: groups
          .map((g) => Padding(
                padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
                child: _capabilityCard(theme, colors, g),
              ))
          .toList(),
    );
  }

  Widget _feedbackPane(ThemeData theme, AppThemeColors colors) {
    final groups = _groupedFeedback();
    if (groups.isEmpty) {
      return const EmptyState(
        title: 'No pipeline feedback yet',
        message: 'When the writer hits prompt confusion, bad source data, or tool '
            'friction, it can report it via report_compose_issue. Nothing recorded yet.',
        icon: Icons.feedback_outlined,
      );
    }
    return ListView(
      children: groups
          .map((g) => Padding(
                padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
                child: _feedbackCard(theme, colors, g),
              ))
          .toList(),
    );
  }

  Widget _capabilityCard(ThemeData theme, AppThemeColors colors, _CapabilityGroup g) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  g.label,
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                decoration: BoxDecoration(
                  color: theme.colorScheme.primary.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  '${g.entries.length}× requested',
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.primary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ...g.displayEntries().map((e) => _reasonRow(theme, colors, e)),
          if (g.entries.length > g.displayEntries().length)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '+ ${g.entries.length - g.displayEntries().length} more',
                style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
              ),
            ),
        ],
      ),
    );
  }

  Widget _feedbackCard(ThemeData theme, AppThemeColors colors, _FeedbackGroup g) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  g.label.replaceAll('_', ' '),
                  style: theme.textTheme.titleSmall?.copyWith(
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              Text(
                '${g.entries.length} report${g.entries.length == 1 ? '' : 's'}',
                style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ...g.displayEntries().map((e) => _feedbackRow(theme, colors, e)),
        ],
      ),
    );
  }

  Widget _reasonRow(ThemeData theme, AppThemeColors colors, Map<String, dynamic> e) {
    final reason = (e['reason']?.toString() ?? '').trim();
    final source = (e['source_url']?.toString() ?? '').trim();
    final date = (e['created_at']?.toString() ?? '').split('T').first;
    final meta = [
      if (date.isNotEmpty) date,
      if (source.isNotEmpty) source,
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (reason.isNotEmpty)
            Text(reason, style: theme.textTheme.bodySmall?.copyWith(height: 1.4)),
          if (meta.isNotEmpty)
            Text(
              meta,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
            ),
        ],
      ),
    );
  }

  Widget _feedbackRow(ThemeData theme, AppThemeColors colors, Map<String, dynamic> e) {
    final summary = (e['summary']?.toString() ?? '').trim();
    final detail = (e['detail']?.toString() ?? '').trim();
    final severity = (e['severity']?.toString() ?? '').trim();
    final tool = (e['related_tool']?.toString() ?? '').trim();
    final source = (e['source_url']?.toString() ?? '').trim();
    final date = (e['created_at']?.toString() ?? '').split('T').first;
    final meta = [
      if (severity.isNotEmpty) severity,
      if (tool.isNotEmpty) tool,
      if (date.isNotEmpty) date,
      if (source.isNotEmpty) source,
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (summary.isNotEmpty)
            Text(
              summary,
              style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
            ),
          if (detail.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                detail,
                style: theme.textTheme.bodySmall?.copyWith(height: 1.4),
              ),
            ),
          if (meta.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                meta,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
              ),
            ),
        ],
      ),
    );
  }
}

class _CapabilityGroup {
  _CapabilityGroup({required this.label});

  final String label;
  final List<Map<String, dynamic>> entries = [];

  List<Map<String, dynamic>> displayEntries() => entries.take(5).toList();
}

class _FeedbackGroup {
  _FeedbackGroup({required this.label});

  final String label;
  final List<Map<String, dynamic>> entries = [];

  List<Map<String, dynamic>> displayEntries() => entries.take(8).toList();
}
