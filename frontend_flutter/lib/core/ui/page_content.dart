import 'package:flutter/material.dart';

import 'layout.dart';

/// Constrains child to the standard content column width.
class PageContent extends StatelessWidget {
  const PageContent({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: AppLayout.maxContentWidth),
        child: child,
      ),
    );
  }
}

/// Scrollable page with padding and optional pull-to-refresh.
EdgeInsets responsivePagePadding(BuildContext context) {
  final width = MediaQuery.sizeOf(context).width;
  if (width < 520) {
    return const EdgeInsets.fromLTRB(16, 16, 16, 32);
  }
  return AppLayout.pagePadding;
}

class PageScroll extends StatelessWidget {
  const PageScroll({
    super.key,
    required this.children,
    this.controller,
    this.refresh,
  });

  final List<Widget> children;
  final ScrollController? controller;
  final Future<void> Function()? refresh;

  @override
  Widget build(BuildContext context) {
    final list = ListView(
      controller: controller,
      padding: responsivePagePadding(context),
      children: [
        Align(
          alignment: Alignment.topCenter,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: AppLayout.maxContentWidth),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: children,
            ),
          ),
        ),
      ],
    );

    if (refresh == null) {
      return list;
    }
    return RefreshIndicator(onRefresh: refresh!, child: list);
  }
}
