import 'package:flutter/material.dart';

import 'site_footer.dart';

/// A scrollable page whose [SiteFooter] is pinned to the bottom of the viewport
/// when content is short, and flows naturally after content when it's tall.
///
/// Uses SliverFillRemaining(hasScrollBody: false) so the footer sticks down
/// without the cost/fragility of IntrinsicHeight.
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
    final view = CustomScrollView(
      controller: controller,
      slivers: [
        SliverToBoxAdapter(child: content),
        SliverFillRemaining(
          hasScrollBody: false,
          child: Column(
            children: const [
              Spacer(),
              SiteFooter(),
            ],
          ),
        ),
      ],
    );

    if (onRefresh == null) return view;
    return RefreshIndicator(onRefresh: onRefresh!, child: view);
  }
}
