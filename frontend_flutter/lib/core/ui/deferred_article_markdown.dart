import 'package:flutter/material.dart';

import '../../core/deferred/deferred_load_pool.dart';
import '../../shared/widgets/deferred_widget.dart';
import 'markdown_deferred_gate.dart';

/// Article body renderer that loads the markdown chunk on demand.
class DeferredArticleMarkdown extends StatelessWidget {
  const DeferredArticleMarkdown({
    super.key,
    required this.data,
    this.selectable = true,
  });

  final String data;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    if (data.trim().isEmpty) {
      return const SizedBox.shrink();
    }
    return DeferredWidget(
      () => loadDeferredWithRetry(loadMarkdownModule),
      () => buildArticleMarkdown(data: data, selectable: selectable),
      placeholder: _BodyPlaceholder(data: data),
    );
  }
}

/// Plain-text stand-in while the markdown chunk downloads.
class _BodyPlaceholder extends StatelessWidget {
  const _BodyPlaceholder({required this.data});

  final String data;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final plain = data.replaceAll(RegExp(r'[#*`\[\]()>_-]'), '').trim();
    return Text(
      plain,
      style: theme.textTheme.bodyLarge?.copyWith(height: 1.7),
    );
  }
}
