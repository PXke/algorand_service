import 'dart:async';

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

const _kActiveStatuses = {'researching', 'writing'};
const _kActivePollInterval = Duration(seconds: 8);
const _kIdlePollInterval = Duration(seconds: 30);

/// Admin tab: recent article-compose sessions — the writer's system/user
/// prompt, each assistant turn, every tool call + result, and the final
/// output. For debugging/analysing what the model actually did.
///
/// The list view is summary-only (status/timing); a session's full transcript
/// is fetched on demand when it's expanded, not on every poll — the messages
/// blob can be up to ~140KB per session, and re-fetching + re-rendering all
/// of that every few seconds for sessions nobody is looking at is the reason
/// this tab used to feel slow.
class ComposeSessionsTab extends ConsumerStatefulWidget {
  const ComposeSessionsTab({super.key});

  @override
  ConsumerState<ComposeSessionsTab> createState() => _ComposeSessionsTabState();
}

class _ComposeSessionsTabState extends ConsumerState<ComposeSessionsTab> {
  List<Map<String, dynamic>> _items = const [];
  final Map<String, Map<String, dynamic>> _details = {};
  final Set<String> _detailLoading = {};
  final Set<String> _detailErrors = {};
  final Set<String> _expandedSessionIds = {};
  bool _loading = true;
  String? _error;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  bool get _hasActiveSession =>
      _items.any((s) => _kActiveStatuses.contains(s['status']?.toString()));

  void _scheduleNextPoll() {
    _poll?.cancel();
    // Live progress needs a tight loop (compose can take minutes and status
    // advances researching -> writing -> ok); once nothing is in flight there's
    // nothing to catch except a brand-new session starting, so back off.
    final interval = _hasActiveSession ? _kActivePollInterval : _kIdlePollInterval;
    _poll = Timer(interval, _quietReload);
  }

  Future<void> _quietReload() async {
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    try {
      final items =
          await ref.read(adminApiProvider).listComposeSessions(walletAddress: wallet);
      if (!mounted) return;
      setState(() => _items = items);
      _refreshExpandedActiveDetails();
    } catch (_) {
      // Silent — the next tick retries; manual refresh surfaces errors.
    } finally {
      if (mounted) _scheduleNextPoll();
    }
  }

  /// A session still `researching`/`writing` that the admin has expanded
  /// should keep advancing on screen, so re-fetch its transcript on every
  /// poll while both conditions hold; anything else (collapsed, or already
  /// finished) keeps whatever was fetched once and is never re-fetched.
  void _refreshExpandedActiveDetails() {
    for (final s in _items) {
      final sessionId = s['session_id']?.toString() ?? '';
      final createdAt = s['created_at']?.toString() ?? '';
      if (sessionId.isEmpty || createdAt.isEmpty) continue;
      if (!_expandedSessionIds.contains(sessionId)) continue;
      if (!_kActiveStatuses.contains(s['status']?.toString())) continue;
      _loadDetail(sessionId, createdAt, force: true);
    }
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
    _poll?.cancel();
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items =
          await ref.read(adminApiProvider).listComposeSessions(walletAddress: wallet);
      if (!mounted) return;
      setState(() {
        _items = items;
        _loading = false;
      });
      _refreshExpandedActiveDetails();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    } finally {
      if (mounted) _scheduleNextPoll();
    }
  }

  Future<void> _loadDetail(String sessionId, String createdAt, {bool force = false}) async {
    if (_detailLoading.contains(sessionId)) return;
    if (!force && _details.containsKey(sessionId)) return;
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    setState(() {
      _detailLoading.add(sessionId);
      _detailErrors.remove(sessionId);
    });
    try {
      final detail = await ref.read(adminApiProvider).getComposeSessionDetail(
            walletAddress: wallet,
            sessionId: sessionId,
            createdAt: createdAt,
          );
      if (!mounted) return;
      setState(() => _details[sessionId] = detail);
    } catch (_) {
      if (!mounted) return;
      setState(() => _detailErrors.add(sessionId));
    } finally {
      if (mounted) setState(() => _detailLoading.remove(sessionId));
    }
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
                  'Recent article composes — expand one for its full transcript '
                  '(prompts, tool calls, output). Newest first, last ~20.',
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
          if (!_loading && _items.isEmpty)
            const EmptyState(
              title: 'No compose sessions yet',
              message: 'Once the writer composes an article, its full transcript '
                  'shows up here. Nothing composed recently.',
              icon: Icons.forum_outlined,
            ),
          ..._items.map((s) => Padding(
                key: ValueKey(s['session_id']),
                padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
                child: _sessionCard(theme, colors, s),
              )),
        ],
      ),
    );
  }

  Widget _sessionCard(ThemeData theme, AppThemeColors colors, Map<String, dynamic> s) {
    final sessionId = s['session_id']?.toString() ?? '';
    final createdAt = s['created_at']?.toString() ?? '';
    final source = (s['source_url']?.toString() ?? '').trim();
    final model = s['model']?.toString() ?? '';
    final status = s['status']?.toString() ?? '';
    final rounds = s['rounds']?.toString() ?? '0';
    final toolCalls = s['tool_calls']?.toString() ?? '0';
    final durationMs = (s['duration_ms'] as num?)?.toInt() ?? 0;
    final date = createdAt.replaceFirst('T', ' ').split('.').first;

    return Container(
      decoration: BoxDecoration(
        color: theme.cardTheme.color,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: colors.border),
      ),
      clipBehavior: Clip.antiAlias,
      child: Theme(
        data: theme.copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
          childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
          onExpansionChanged: (expanded) {
            if (expanded) {
              _expandedSessionIds.add(sessionId);
              if (sessionId.isNotEmpty && createdAt.isNotEmpty) {
                _loadDetail(sessionId, createdAt);
              }
            } else {
              _expandedSessionIds.remove(sessionId);
            }
          },
          title: Text(
            source.isEmpty ? '(no source)' : source,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
          subtitle: Text(
            '$date · $model · ${rounds}r · $toolCalls tools · ${(durationMs / 1000).toStringAsFixed(1)}s · $status',
            style: theme.textTheme.labelSmall?.copyWith(color: colors.subtle),
          ),
          children: [_sessionDetail(theme, colors, sessionId)],
        ),
      ),
    );
  }

  Widget _sessionDetail(ThemeData theme, AppThemeColors colors, String sessionId) {
    if (_detailLoading.contains(sessionId)) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: LoadingStrip(visible: true),
      );
    }
    if (_detailErrors.contains(sessionId)) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          'Failed to load transcript — try re-expanding.',
          style: theme.textTheme.bodySmall?.copyWith(color: colors.muted),
        ),
      );
    }
    final detail = _details[sessionId];
    if (detail == null) return const SizedBox.shrink();
    final messages = (detail['messages'] as List?) ?? const [];
    final finalOutput = detail['final_output']?.toString() ?? '';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ...messages.whereType<Map>().map((m) => _messageRow(theme, colors, m)),
        if (finalOutput.isNotEmpty) ...[
          const SizedBox(height: 8),
          _label(theme, 'FINAL OUTPUT'),
          _mono(theme, colors, finalOutput, colors.accent),
        ],
      ],
    );
  }

  Widget _messageRow(ThemeData theme, AppThemeColors colors, Map m) {
    final role = m['role']?.toString() ?? '';
    final content = m['content']?.toString() ?? '';
    final name = m['name']?.toString() ?? '';
    final toolCalls = (m['tool_calls'] as List?) ?? const [];
    final roleColor = switch (role) {
      'system' => colors.muted,
      'user' => const Color(0xFF2E7D32),
      'assistant' => theme.colorScheme.primary,
      'tool' => const Color(0xFFB7791F),
      _ => colors.subtle,
    };
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _label(theme, name.isNotEmpty ? '$role · $name' : role, roleColor),
          for (final tc in toolCalls.whereType<Map>())
            _mono(
              theme,
              colors,
              '→ ${tc['name']}(${tc['arguments']})',
              theme.colorScheme.primary,
            ),
          if (content.isNotEmpty) _mono(theme, colors, content, null),
        ],
      ),
    );
  }

  Widget _label(ThemeData theme, String text, [Color? color]) => Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Text(
          text.toUpperCase(),
          style: theme.textTheme.labelSmall?.copyWith(
            fontWeight: FontWeight.w700,
            letterSpacing: 0.5,
            color: color,
          ),
        ),
      );

  Widget _mono(ThemeData theme, AppThemeColors colors, String text, Color? color) =>
      Container(
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 4),
        padding: const EdgeInsets.all(8),
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.4),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(
          text,
          style: theme.textTheme.bodySmall?.copyWith(
            fontFamily: 'monospace',
            fontSize: 12,
            height: 1.4,
            color: color,
          ),
        ),
      );
}
