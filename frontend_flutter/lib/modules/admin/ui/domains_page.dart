import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../core/l10n/l10n_extensions.dart';
import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/empty_state.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../auth/providers/auth_providers.dart';

/// Admin tab: crawl frontier — which domains the crawler may explore.
class DomainsTab extends ConsumerStatefulWidget {
  const DomainsTab({super.key});

  @override
  ConsumerState<DomainsTab> createState() => _DomainsTabState();
}

class _DomainsTabState extends ConsumerState<DomainsTab> {
  List<Map<String, dynamic>> _items = const [];
  int _autoApprovedToday = 0;
  bool _loading = true;
  String? _error;
  String _filter = 'all';
  final Set<String> _busy = {};

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
        _error = context.l10n.domainsWalletNotConnected;
      });
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result =
          await ref.read(adminApiProvider)
              .listDomains(walletAddress: wallet, status: _filter);
      if (!mounted) return;
      setState(() {
        _items = result.items;
        _autoApprovedToday = result.autoApprovedToday;
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

  Future<void> _set(Map<String, dynamic> item, {required bool relevant}) async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    final domain = item['domain']?.toString() ?? '';
    if (wallet == null || domain.isEmpty) return;
    final makeRelevant = relevant;
    setState(() => _busy.add(domain));
    try {
      await ref.read(adminApiProvider).setDomainRelevant(
        walletAddress: wallet,
        domain: domain,
        isRelevant: makeRelevant,
      );
      await _load();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            makeRelevant
                ? context.l10n.domainsApprovedSnack(domain)
                : context.l10n.domainsDeadEndSnack(domain),
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _busy.remove(domain));
    }
  }

  Future<void> _openInNewTab(String url) async {
    var target = url.trim();
    if (target.isEmpty) return;
    if (!target.startsWith('http://') && !target.startsWith('https://')) {
      target = 'https://$target';
    }
    final uri = Uri.tryParse(target);
    if (uri == null) return;
    // webOnlyWindowName '_blank' opens a new browser tab on Flutter web.
    await launchUrl(uri, mode: LaunchMode.externalApplication, webOnlyWindowName: '_blank');
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final l10n = context.l10n;
    // Server-side filtered, so show the count only for the active view.
    final shown = _items.length;
    final visible = _items;

    return PageScroll(
      refresh: _load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                l10n.domainsIntro,
                style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
              ),
            ),
            if (_autoApprovedToday > 0)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Tooltip(
                  message: 'Domains the frontier auto-approved today (UTC)',
                  child: Text(
                    'Auto-approved today: $_autoApprovedToday',
                    style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
                  ),
                ),
              ),
            IconButton(
              tooltip: l10n.actionRefresh,
              iconSize: 18,
              visualDensity: VisualDensity.compact,
              onPressed: _loading ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        const SizedBox(height: AppLayout.itemGap),
        Wrap(
          spacing: 8,
          children: [
            FilterChip(
              label: Text(
                _filter == 'all' ? l10n.domainsFilterAllCount(shown) : l10n.domainsFilterAll,
              ),
              selected: _filter == 'all',
              onSelected: (_) { setState(() => _filter = 'all'); _load(); },
            ),
            FilterChip(
              label: Text(
                _filter == 'pending'
                    ? l10n.domainsFilterPendingCount(shown)
                    : l10n.domainsFilterPending,
              ),
              selected: _filter == 'pending',
              onSelected: (_) { setState(() => _filter = 'pending'); _load(); },
            ),
            FilterChip(
              label: Text(
                _filter == 'dead_end'
                    ? l10n.domainsFilterDeadEndsCount(shown)
                    : l10n.domainsFilterDeadEnds,
              ),
              selected: _filter == 'dead_end',
              onSelected: (_) { setState(() => _filter = 'dead_end'); _load(); },
            ),
          ],
        ),
        const SizedBox(height: AppLayout.itemGap),
        LoadingStrip(visible: _loading),
        if (_error != null) ErrorBanner(message: _error!),
        if (!_loading && visible.isEmpty)
          EmptyState(
            title: l10n.domainsEmptyTitle,
            message: l10n.domainsEmptyMessage,
            icon: Icons.travel_explore_outlined,
          ),
        ...visible.map((item) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _domainCard(theme, colors, item),
            )),
      ],
    );
  }

  /// Content-relevance score (0-1) from the crawled page text — a review AID
  /// (queue is sorted by it), not an auto-decision. Green = likely relevant.
  Widget _relevanceChip(ThemeData theme, double score) {
    final color = score >= 0.4
        ? const Color(0xFF2E7D32)
        : score >= 0.2
            ? const Color(0xFFB7791F)
            : const Color(0xFF9AA0A6);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        'rel ${score.toStringAsFixed(2)}',
        style: theme.textTheme.labelSmall?.copyWith(color: color, fontWeight: FontWeight.w700),
      ),
    );
  }

  Widget _domainCard(ThemeData theme, AppThemeColors colors, Map<String, dynamic> item) {
    final l10n = context.l10n;
    final domain = item['domain']?.toString() ?? '';
    final relevant = item['is_relevant'] == true;
    final pending = item['frontier_status'] == 'pending';
    final pendingUrl = item['pending_url']?.toString() ?? '';
    final score = (item['relevance_score'] as num?)?.toDouble() ?? 0;
    final category = item['category']?.toString() ?? '';
    final lastCrawled = (item['last_crawled_at']?.toString() ?? '').split('T').first;
    final pagesCrawled = (item['pages_crawled'] as num?)?.toInt() ?? 0;
    final busy = _busy.contains(domain);
    final statusColor = pending
        ? const Color(0xFFB7791F)
        : relevant
            ? const Color(0xFF2E7D32)
            : theme.colorScheme.error;

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
              Container(
                width: 9,
                height: 9,
                decoration: BoxDecoration(color: statusColor, shape: BoxShape.circle),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Tooltip(
                  message: l10n.domainsOpenInNewTab(domain),
                  child: InkWell(
                    onTap: domain.isEmpty ? null : () => _openInNewTab(pendingUrl.isNotEmpty ? pendingUrl : domain),
                    borderRadius: BorderRadius.circular(4),
                    child: Text(
                      domain,
                      style: theme.textTheme.bodyMedium?.copyWith(
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.w600,
                        color: theme.colorScheme.primary,
                        decoration: TextDecoration.underline,
                        decorationColor: theme.colorScheme.primary.withValues(alpha: 0.4),
                      ),
                    ),
                  ),
                ),
              ),
              if (item['content_relevance'] != null) ...[
                const SizedBox(width: 6),
                _relevanceChip(theme, (item['content_relevance'] as num).toDouble()),
              ],
              const SizedBox(width: 6),
              Icon(Icons.open_in_new, size: 18, color: colors.muted),
            ],
          ),
          const SizedBox(height: 6),
          if (pending) ...[
            if ((item['preview_title'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  item['preview_title'] as String? ?? '',
                  style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                ),
              ),
            if ((item['preview_description'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(
                  item['preview_description'] as String? ?? '',
                  style: theme.textTheme.bodySmall?.copyWith(height: 1.45),
                ),
              ),
            if ((item['preview_keywords'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(
                  l10n.domainsKeywords(item['preview_keywords'] as String? ?? ''),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: colors.muted,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ),
            if ((item['link_text'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  l10n.domainsLinkedAs(item['link_text'] as String? ?? ''),
                  style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
                ),
              ),
            Text(
              [
                l10n.domainsPredictedInterest(score.toStringAsFixed(1)),
                if (pendingUrl.isNotEmpty) pendingUrl,
                if ((item['found_on'] as String? ?? '').isNotEmpty)
                  l10n.domainsFoundOn(item['found_on'] as String? ?? ''),
              ].join('\n'),
              style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle, height: 1.5),
            ),
          ] else
            Text(
              [
                l10n.domainsScore(score.toStringAsFixed(1)),
                if (category.isNotEmpty) category,
                l10n.domainsPagesCrawled(pagesCrawled),
                if (lastCrawled.isNotEmpty) l10n.domainsCrawled(lastCrawled),
              ].join(' · '),
              style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
            ),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              if (pending) ...[
                OutlinedButton(
                  onPressed: busy ? null : () => _set(item, relevant: false),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: theme.colorScheme.error,
                    visualDensity: VisualDensity.compact,
                  ),
                  child: Text(l10n.domainsDeadEnd),
                ),
                const SizedBox(width: 8),
                FilledButton.tonal(
                  onPressed: busy ? null : () => _set(item, relevant: true),
                  style: FilledButton.styleFrom(visualDensity: VisualDensity.compact),
                  child: busy
                      ? const SizedBox(
                          width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                      : Text(l10n.domainsApproveExplore),
                ),
              ] else
                OutlinedButton(
                  onPressed: busy ? null : () => _set(item, relevant: !relevant),
                  style: OutlinedButton.styleFrom(
                    foregroundColor:
                        relevant ? theme.colorScheme.error : theme.colorScheme.primary,
                    visualDensity: VisualDensity.compact,
                  ),
                  child: busy
                      ? const SizedBox(
                          width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                      : Text(relevant ? l10n.domainsMarkDeadEnd : l10n.domainsRevive),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
