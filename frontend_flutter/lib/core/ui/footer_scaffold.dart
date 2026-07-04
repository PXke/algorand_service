import 'package:flutter/material.dart';

import 'site_footer.dart';

/// A scrollable page whose [SiteFooter] is pinned to the bottom of the viewport
/// when content is short, and flows naturally after content when it's tall.
class FooterScaffold extends StatelessWidget {
  const FooterScaffold({
    super.key,
    required this.content,
    this.controller,
    this.onRefresh,
  });

  /// The page body (already padded / width-constrained by the caller).
  final Widget content;
  final ScrollController? controller;
  final Future<void> Function()? onRefresh;

  @override
  Widget build(BuildContext context) {
    final scroll = LayoutBuilder(
      builder: (context, constraints) {
        return SingleChildScrollView(
          controller: controller,
          physics: onRefresh != null
              ? const AlwaysScrollableScrollPhysics()
              : null,
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: constraints.maxHeight),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                content,
                const SiteFooter(),
              ],
            ),
          ),
        );
      },
    );

    if (onRefresh == null) return scroll;
    return RefreshIndicator(onRefresh: onRefresh!, child: scroll);
  }
}
