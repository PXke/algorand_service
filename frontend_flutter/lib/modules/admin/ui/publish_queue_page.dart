import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/session_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';

/// Admin tab: the publish queue with each row's status and the persisted
/// last drain/compose decision (last_reason) — "why is/was this row here".
/// A row expands to its enqueue-time priority breakdown + content signals.
class PublishQueueTab extends ConsumerStatefulWidget {
  const PublishQueueTab({super.key});

  @override
  ConsumerState<PublishQueueTab> createState() => _PublishQueueTabState();
}

class _PublishQueueTabState extends ConsumerState<PublishQueueTab> {
  List<Map<String, dynamic>> _items = const [];
  List<Map<String, dynamic>> _backlog = const [];
  final Map<String, Map<String, dynamic>> _breakdowns = {};
  final Set<String> _breakdownLoading = {};
  final Set<String> _expanded = {};
  bool _loading = true;
  String? _error;
  String _statusFilter = 'all';

  static const _statusFilters = ['all', 'pending', 'done', 'deferred', 'expired'];

  @override
  void initState() {
    super.initState();
    _load();
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
      final items =
          await ref.read(adminApiProvider).listPublishQueue(walletAddress: wallet);
      // Best-effort: the backlog panel is a bonus view, not the tab's core
      // job — a failure here must not blank out the main queue list.
      List<Map<String, dynamic>> backlog = const [];
      try {
        backlog = await ref
            .read(adminApiProvider)
            .listPendingFeedBacklog(walletAddress: wallet);
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _items = items;
        _backlog = backlog;
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

  Future<void> _toggleExpanded(String queueId) async {
    if (_expanded.contains(queueId)) {
      setState(() => _expanded.remove(queueId));
      return;
    }
    setState(() => _expanded.add(queueId));
    if (_breakdowns.containsKey(queueId) || _breakdownLoading.contains(queueId)) {
      return;
    }
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    setState(() => _breakdownLoading.add(queueId));
    try {
      final detail = await ref
          .read(adminApiProvider)
          .publishQueueBreakdown(walletAddress: wallet, queueId: queueId);
      if (!mounted) return;
      setState(() {
        _breakdowns[queueId] = detail;
        _breakdownLoading.remove(queueId);
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _breakdownLoading.remove(queueId));
    }
  }

  List<Map<String, dynamic>> get _visible => _statusFilter == 'all'
      ? _items
      : _items
          .where((it) => (it['status']?.toString() ?? '') == _statusFilter)
          .toList();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final visible = _visible;

    return PageScroll(
      refresh: _load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Publish-queue rows with the last drain/compose decision each '
                'one received — newest activity first.',
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
        if (_backlog.isNotEmpty) ...[
          _PendingReleasePanel(items: _backlog),
          const SizedBox(height: AppLayout.itemGap),
        ],
        Wrap(
          spacing: 8,
          children: _statusFilters
              .map(
                (status) => ChoiceChip(
                  label: Text(status),
                  selected: _statusFilter == status,
                  visualDensity: VisualDensity.compact,
                  onSelected: (_) => setState(() => _statusFilter = status),
                ),
              )
              .toList(),
        ),
        const SizedBox(height: AppLayout.itemGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (!_loading && visible.isEmpty && _error == null)
          const EmptyState(
            title: 'Queue is empty',
            message: 'No publish-queue rows match this filter.',
            icon: Icons.playlist_remove_outlined,
          ),
        ...visible.map(
          (item) {
            final queueId = item['queue_id']?.toString() ?? '';
            return Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _QueueRowCard(
                item: item,
                expanded: _expanded.contains(queueId),
                breakdown: _breakdowns[queueId],
                breakdownLoading: _breakdownLoading.contains(queueId),
                onTap: () => _toggleExpanded(queueId),
              ),
            );
          },
        ),
      ],
    );
  }
}

/// Approved articles already composed, waiting in pending_feed_queue for the
/// paced-release worker to publish them (PENDING_FEED_MAX_DEPTH caps this —
/// default 3). Distinct from the rows below, which are still COMPOSING.
class _PendingReleasePanel extends StatelessWidget {
  const _PendingReleasePanel({required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;

    return Container(
      padding: const EdgeInsets.all(14),
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
              Icon(Icons.hourglass_bottom, size: 16, color: theme.colorScheme.primary),
              const SizedBox(width: 8),
              Text('Pending release (${items.length})', style: theme.textTheme.titleSmall),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            'Approved and composed — waiting for the paced-release worker to '
            'publish them.',
            style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
          ),
          const SizedBox(height: 10),
          ...items.map((item) {
            final title = item['title']?.toString() ?? '';
            final serviceId = item['service_id']?.toString() ?? '';
            final approvedAt =
                (item['approved_at']?.toString() ?? '').replaceFirst('T', ' ');
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      title.isEmpty ? serviceId : title,
                      style: theme.textTheme.bodySmall,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    approvedAt.length > 16 ? approvedAt.substring(0, 16) : approvedAt,
                    style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }
}

class _QueueRowCard extends StatelessWidget {
  const _QueueRowCard({
    required this.item,
    required this.expanded,
    required this.breakdown,
    required this.breakdownLoading,
    required this.onTap,
  });

  final Map<String, dynamic> item;
  final bool expanded;
  final Map<String, dynamic>? breakdown;
  final bool breakdownLoading;
  final VoidCallback onTap;

  Color _statusColor(BuildContext context, String status) {
    final scheme = Theme.of(context).colorScheme;
    switch (status) {
      case 'pending':
        return Colors.amber;
      case 'done':
        return Colors.green;
      case 'expired':
      case 'deferred':
        return Theme.of(context).disabledColor;
      default:
        return scheme.primary;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final status = item['status']?.toString() ?? '';
    final reason = item['last_reason']?.toString() ?? '';
    final priority = (item['priority'] as num?)?.toInt() ?? 0;
    final displayName = item['display_name']?.toString() ?? '';
    final serviceId = item['service_id']?.toString() ?? '';
    final kind = item['publish_kind']?.toString() ?? '';
    final topic = item['topic']?.toString() ?? '';
    final url = item['scrape_url']?.toString() ?? '';
    final updated = (item['updated_at']?.toString() ?? '').replaceFirst('T', ' ');

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.all(14),
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
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: _statusColor(context, status),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(status, style: theme.textTheme.labelMedium),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    displayName.isEmpty ? serviceId : displayName,
                    style: theme.textTheme.titleSmall,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(
                  'prio $priority',
                  style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                ),
              ],
            ),
            const SizedBox(height: 6),
            if (reason.isNotEmpty)
              Text(
                'last decision: $reason',
                style: theme.textTheme.bodySmall?.copyWith(
                  fontFamily: 'monospace',
                  color: theme.colorScheme.primary,
                ),
              ),
            const SizedBox(height: 4),
            Text(
              [kind, topic, url].where((s) => s.isNotEmpty).join(' · '),
              style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
              overflow: TextOverflow.ellipsis,
            ),
            if (updated.isNotEmpty)
              Text(
                updated.length > 16 ? updated.substring(0, 16) : updated,
                style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
              ),
            if (expanded) ...[
              const Divider(height: 20),
              if (breakdownLoading)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 8),
                  child: LinearProgressIndicator(minHeight: 2),
                )
              else if (breakdown != null)
                _BreakdownDetail(breakdown: breakdown!)
              else
                Text(
                  'No breakdown available for this row.',
                  style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _BreakdownDetail extends StatelessWidget {
  const _BreakdownDetail({required this.breakdown});

  final Map<String, dynamic> breakdown;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final priorityBreakdown = breakdown['priority_breakdown']?.toString() ?? '';
    final signals = breakdown['signals'];
    final diff = breakdown['diff_preview']?.toString() ?? '';

    String signalsText = '';
    if (signals is Map && signals.isNotEmpty) {
      signalsText = signals.entries
          .map((e) => '${e.key}=${e.value}')
          .join('  ');
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (priorityBreakdown.isNotEmpty) ...[
          Text('Priority breakdown', style: theme.textTheme.labelMedium),
          const SizedBox(height: 4),
          SelectableText(
            priorityBreakdown,
            style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
          ),
          const SizedBox(height: 10),
        ],
        if (signalsText.isNotEmpty) ...[
          Text('Content signals', style: theme.textTheme.labelMedium),
          const SizedBox(height: 4),
          SelectableText(
            signalsText,
            style: theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
          ),
          const SizedBox(height: 10),
        ],
        if (diff.isNotEmpty) ...[
          Text('Diff preview', style: theme.textTheme.labelMedium),
          const SizedBox(height: 4),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: colors.panelBackground,
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: colors.border),
            ),
            child: SelectableText(
              diff,
              maxLines: 30,
              style: theme.textTheme.bodySmall?.copyWith(
                fontFamily: 'monospace',
                fontSize: 11,
                height: 1.4,
              ),
            ),
          ),
        ],
      ],
    );
  }
}
