import 'package:flutter/material.dart';

/// Loads a deferred library on demand, then builds its widget once the chunk
/// is downloaded — showing a lightweight placeholder meanwhile.
class DeferredWidget extends StatefulWidget {
  const DeferredWidget(
    this.libraryLoader,
    this.createWidget, {
    super.key,
    this.placeholder,
  });

  /// Returns a future that loads the deferred library (optionally retried).
  final Future<void> Function() libraryLoader;

  final Widget Function() createWidget;

  final Widget? placeholder;

  @override
  State<DeferredWidget> createState() => _DeferredWidgetState();
}

class _DeferredWidgetState extends State<DeferredWidget> {
  late final Future<void> _loading = widget.libraryLoader();

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<void>(
      future: _loading,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.done) {
          if (snapshot.hasError) {
            return Center(child: Text('Failed to load: ${snapshot.error}'));
          }
          return widget.createWidget();
        }
        return widget.placeholder ??
            const Center(child: CircularProgressIndicator());
      },
    );
  }
}
