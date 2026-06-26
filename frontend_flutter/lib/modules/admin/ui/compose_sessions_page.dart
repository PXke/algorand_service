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

/// Admin tab: full agentic transcript of recent article composes — the writer's
/// system/user prompt, each assistant turn, every tool call + result, and the
/// final output. For debugging/analysing what the model actually did.
class ComposeSessionsTab extends ConsumerStatefulWidget {
  const ComposeSessionsTab({super.key});

  @override
  ConsumerState<ComposeSessionsTab> createState() => _ComposeSessionsTabState();
}

class _ComposeSessionsTabState extends ConsumerState<ComposeSessionsTab> {
  List<Map<String, dynamic>> _items = const [];
  bool _loading = true;
  String? _error;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
    // Live progress: compose now takes minutes, so quietly re-poll so an
    // in-progress session advances (researching -> writing -> ok) on screen.
    _poll = Timer.periodic(const Duration(seconds: 8), (_) => _quietReload());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _quietReload() async {
    if (_loading) return;
    final wallet = ref.read(walletAuthStateProvider).walletAddress;
    if (wallet == null || wallet.isEmpty) return;
    try {
      final items =
          await ref.read(adminApiProvider).listComposeSessions(walletAddress: wallet);
      if (!mounted) return;
      setState(() => _items = items);
    } catch (_) {
      // Silent — the next tick retries; manual refresh surfaces errors.
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
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
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
                  'Full transcript of recent article composes — prompts, every tool '
                  'call + result, and the final output. Newest first, last ~20.',
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
                padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
                child: _sessionCard(theme, colors, s),
              )),
        ],
      ),
    );
  }

  Widget _sessionCard(ThemeData theme, AppThemeColors colors, Map<String, dynamic> s) {
    final source = (s['source_url']?.toString() ?? '').trim();
    final model = s['model']?.toString() ?? '';
    final status = s['status']?.toString() ?? '';
    final rounds = s['rounds']?.toString() ?? '0';
    final toolCalls = s['tool_calls']?.toString() ?? '0';
    final durationMs = (s['duration_ms'] as num?)?.toInt() ?? 0;
    final date = (s['created_at']?.toString() ?? '').replaceFirst('T', ' ').split('.').first;
    final messages = (s['messages'] as List?) ?? const [];
    final finalOutput = s['final_output']?.toString() ?? '';

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
          children: [
            ...messages.whereType<Map>().map((m) => _messageRow(theme, colors, m)),
            if (finalOutput.isNotEmpty) ...[
              const SizedBox(height: 8),
              _label(theme, 'FINAL OUTPUT'),
              _mono(theme, colors, finalOutput, colors.accent),
            ],
          ],
        ),
      ),
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
