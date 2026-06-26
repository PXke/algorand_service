import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';

/// Admin tab: capabilities the writer model asked for (via the suggest_tool
/// tool) when the existing tools could not give it data a story needed. Grouped
/// by capability, most-requested first, so we know which tools to build next.
/// (Tool *errors* are reported to Bugsnag, not here.)
class ToolInsightsTab extends ConsumerStatefulWidget {
  const ToolInsightsTab({super.key});

  @override
  ConsumerState<ToolInsightsTab> createState() => _ToolInsightsTabState();
}

class _ToolInsightsTabState extends ConsumerState<ToolInsightsTab> {
  List<Map<String, dynamic>> _items = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
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
      final items =
          await ref.read(adminApiProvider).listToolSuggestions(walletAddress: wallet);
      if (!mounted) return;
      setState(() {
        _items = items;
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

  /// Group by capability (case-insensitive), most-requested first.
  List<_CapabilityGroup> _grouped() {
    final byCap = <String, _CapabilityGroup>{};
    for (final item in _items) {
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

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final groups = _grouped();

    return SelectionArea(
      child: PageScroll(
      refresh: _load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Capabilities the writer asked for while composing — recorded when it '
                'hit a wall the current tools could not solve. Most-requested first; '
                'use this to decide which tools to build next. Tool errors go to Bugsnag.',
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
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (!_loading && groups.isEmpty)
          const EmptyState(
            title: 'No tool suggestions yet',
            message: 'When the writer wishes it had a tool it lacks, it records the gap '
                'here. Nothing yet — that is a good sign the toolset is covering stories.',
            icon: Icons.build_outlined,
          ),
        ...groups.map((g) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _groupCard(theme, colors, g),
            )),
      ],
      ),
    );
  }

  Widget _groupCard(ThemeData theme, AppThemeColors colors, _CapabilityGroup g) {
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
}

class _CapabilityGroup {
  _CapabilityGroup({required this.label});

  final String label;
  final List<Map<String, dynamic>> entries = [];

  /// Show the most recent few reasons; the rest collapse into a "+N more".
  List<Map<String, dynamic>> displayEntries() => entries.take(5).toList();
}
