import 'package:flutter/material.dart';

class LoadingStrip extends StatelessWidget {
  const LoadingStrip({super.key, this.visible = true});

  final bool visible;

  @override
  Widget build(BuildContext context) {
    if (!visible) {
      return const SizedBox.shrink();
    }
    return const Padding(
      padding: EdgeInsets.only(bottom: 16),
      child: LinearProgressIndicator(minHeight: 2),
    );
  }
}
