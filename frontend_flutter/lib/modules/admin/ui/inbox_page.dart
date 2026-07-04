import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/api_providers.dart';
import '../../../core/theme/app_theme_extension.dart';
import '../../../core/ui/error_banner.dart';
import '../../../core/ui/layout.dart';
import '../../../core/ui/loading_strip.dart';
import '../../../core/ui/page_content.dart';
import '../../../core/providers/session_providers.dart';

/// Reader messages from the public /contact form (last two months).
class InboxTab extends ConsumerStatefulWidget {
  const InboxTab({super.key});

  @override
  ConsumerState<InboxTab> createState() => _InboxTabState();
}

class _InboxTabState extends ConsumerState<InboxTab> {
  List<Map<String, dynamic>> _items = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final wallet = ref.read(sessionStateProvider).walletAddress;
    if (wallet == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final items = await ref
          .read(adminApiProvider)
          .listContactMessages(walletAddress: wallet);
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

    return PageScroll(
      refresh: _load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'Messages sent through the public contact form — newest first, '
                'last two months.',
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
        if (!_loading && _items.isEmpty && _error == null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 32),
            child: Column(
              children: [
                Icon(Icons.inbox_outlined, size: 40, color: colors.muted),
                const SizedBox(height: 12),
                Text(
                  'No messages yet.',
                  style: theme.textTheme.bodyMedium?.copyWith(color: colors.muted),
                ),
              ],
            ),
          ),
        ..._items.map((item) => Padding(
              padding: const EdgeInsets.only(bottom: AppLayout.itemGap),
              child: _MessageCard(item: item),
            )),
      ],
    );
  }
}

class _MessageCard extends StatelessWidget {
  const _MessageCard({required this.item});

  final Map<String, dynamic> item;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = context.appColors;
    final name = item['name']?.toString() ?? '';
    final email = item['email']?.toString() ?? '';
    final message = item['message']?.toString() ?? '';
    final epoch = (item['created_at_epoch'] as num?)?.toInt() ?? 0;
    final when = epoch > 0
        ? DateTime.fromMillisecondsSinceEpoch(epoch * 1000)
            .toLocal()
            .toString()
            .substring(0, 16)
        : '';

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
          Row(
            children: [
              Expanded(
                child: Text(
                  name.isEmpty ? 'Anonymous reader' : name,
                  style: theme.textTheme.titleSmall,
                ),
              ),
              Text(
                when,
                style: theme.textTheme.labelSmall?.copyWith(color: colors.muted),
              ),
            ],
          ),
          if (email.isNotEmpty)
            InkWell(
              onTap: () async {
                await Clipboard.setData(ClipboardData(text: email));
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('Email copied')),
                );
              },
              child: Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(
                  email,
                  style: theme.textTheme.labelMedium?.copyWith(
                    color: theme.colorScheme.primary,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
            ),
          const SizedBox(height: 10),
          SelectableText(
            message,
            style: theme.textTheme.bodyMedium?.copyWith(height: 1.5),
          ),
        ],
      ),
    );
  }
}
